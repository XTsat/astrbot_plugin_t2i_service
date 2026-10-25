"""内嵌在 AstrBot 进程内的文转图 HTTP 服务（aiohttp 实现）。

对外保持与上游 astrbot-t2i-service 完全一致的接口契约，这样 AstrBot 主程序
只需要把 ``t2i_endpoint`` 指向 ``http://127.0.0.1:<port>/text2img``：

* ``POST /text2img/generate`` → ``{"code":0,"data":{"id":"data/xx.png"}}`` 或图片字节流
* ``GET  /text2img/data/{id}`` → 图片
* ``GET  /healthz`` → 健康检查（无鉴权，供自检脚本使用）

当本机 Chromium 不可用或渲染失败时，可以选择把请求透传给上游公共端点，
保证插件装上了就能用。
"""

from __future__ import annotations

import asyncio
import secrets
import time

from aiohttp import ClientSession, ClientTimeout, web
from jinja2.exceptions import SecurityError
from loguru import logger
from pydantic import ValidationError

from t2i_core.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    RENDER_INPUT_BYTES,
    RENDER_REQUESTS,
)
from t2i_core.render import PlaywrightNotInstalledError, ScreenshotOptions, Text2ImgRender
from t2i_core.storage import LocalImageStorage

from .browser import BrowserManager, playwright_pip_available
from .config import T2IConfig


def normalize_render_url(url: str) -> str:
    """把任意形式的上游地址归一化成 ``.../text2img``（对齐 AstrBot 主程序逻辑）。"""
    url = (url or "").strip().removesuffix("/")
    if not url:
        return ""
    if not url.endswith("text2img"):
        url += "/text2img"
    return url


class T2IHttpServer:
    """把渲染器、存储以 HTTP 接口暴露给 AstrBot 主程序。"""

    def __init__(
        self,
        config: T2IConfig,
        renderer: Text2ImgRender,
        data_dir: str,
        storage: LocalImageStorage | None = None,
        browser_manager: BrowserManager | None = None,
    ) -> None:
        self.config = config
        self.renderer = renderer
        self.data_dir = data_dir
        self.browser_manager = browser_manager
        self.storage = storage or LocalImageStorage(data_dir=data_dir)
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.bound_port: int | None = None
        self.started_at: float | None = None
        self.last_error: str = ""
        self.fallback_used = 0
        self.requests_served = 0
        self.last_request_at: float | None = None
        self._session: ClientSession | None = None

    # ------------------------------------------------------------ 生命周期

    @property
    def running(self) -> bool:
        return self.runner is not None

    @property
    def endpoint(self) -> str:
        port = self.bound_port or self.config.server.port
        return f"http://{self.config.server.host}:{port}/text2img"

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=16 * 1024 * 1024)
        app.router.add_post("/text2img/generate", self.handle_generate)
        app.router.add_get("/text2img/data/{image_id}", self.handle_data)
        app.router.add_get("/healthz", self.handle_health)
        return app

    async def start(self) -> tuple[bool, str]:
        """启动服务。返回 (是否成功, 说明)。"""
        if self.running:
            return True, f"服务已在运行：{self.endpoint}"

        runner = web.AppRunner(self._build_app(), access_log=None)
        await runner.setup()
        site = web.TCPSite(
            runner, self.config.server.host, self.config.server.port
        )
        try:
            await site.start()
        except OSError as exc:
            await runner.cleanup()
            self.last_error = f"端口 {self.config.server.port} 启动失败：{exc}"
            logger.error(f"[t2i] {self.last_error}")
            return False, self.last_error

        self.runner, self.site = runner, site
        self.started_at = time.time()
        self.bound_port = self._resolve_bound_port(site)
        self.last_error = ""
        logger.info(f"[t2i] 内嵌渲染服务已启动：{self.endpoint}")
        return True, self.endpoint

    @staticmethod
    def _resolve_bound_port(site: web.TCPSite) -> int | None:
        try:
            addresses = site._server.sockets  # type: ignore[union-attr]
            if addresses:
                return int(addresses[0].getsockname()[1])
        except (AttributeError, IndexError, OSError):
            return None
        return None

    async def stop(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
            self.site = None
            logger.info("[t2i] 内嵌渲染服务已停止")

    # ---------------------------------------------------------------- 工具

    def _authorized(self, request: web.Request) -> bool:
        token = self.config.server.auth_token
        if not token:
            return True
        header = request.headers.get("Authorization", "")
        expected = f"Bearer {token}"
        return secrets.compare_digest(header, expected)

    def _error(self, status: int, message: str, **extra) -> web.Response:
        payload = {"code": 1, "message": message, "data": {}}
        payload.update(extra)
        return web.json_response(payload, status=status)

    async def _forward_to_fallback(self, payload: dict) -> web.Response:
        """把请求透传给上游公共端点。"""
        url = normalize_render_url(self.config.server.fallback_endpoint)
        if not url:
            return self._error(503, "本地渲染不可用，且未配置备用端点")
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=120))
        try:
            async with self._session.post(f"{url}/generate", json=payload) as resp:
                body = await resp.read()
                content_type = resp.headers.get("Content-Type", "application/json")
        except Exception as exc:  # 网络异常
            self.last_error = f"备用端点转发失败：{exc}"
            logger.warning(f"[t2i] {self.last_error}")
            return self._error(503, self.last_error)

        self.fallback_used += 1
        logger.info(f"[t2i] 已转发到备用端点 {url}（HTTP {resp.status}）")
        return web.Response(status=resp.status, body=body, content_type=content_type.split(";")[0])

    def _local_render_available(self) -> bool:
        if not playwright_pip_available():
            return False
        if self.browser_manager is not None:
            return self.browser_manager.is_installed()
        return True

    def _build_options(self, raw_options: dict | None) -> ScreenshotOptions:
        render_cfg = self.config.render
        base = ScreenshotOptions(
            type=render_cfg.default_type,  # type: ignore[arg-type]
            quality=render_cfg.default_quality or None,
            full_page=render_cfg.full_page,
        ).model_dump()
        base.update(raw_options or {})
        return ScreenshotOptions(**base)

    # ------------------------------------------------------------- 处理函数

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "service": "astrbot_plugin_t2i_service",
                "endpoint": self.endpoint,
                "uptime": round(time.time() - self.started_at, 1)
                if self.started_at
                else 0,
                "requests": self.requests_served,
                "fallback_used": self.fallback_used,
            }
        )

    async def handle_data(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return self._error(401, "unauthorized")
        image_id = request.match_info.get("image_id", "")
        image = await self.storage.get(image_id)
        if image is None or image.path is None:
            return self._error(404, "file not found")
        return web.FileResponse(image.path, headers={"Content-Type": image.media_type})

    async def handle_generate(self, request: web.Request) -> web.Response:
        started = time.perf_counter()
        status = "500"
        try:
            if not self._authorized(request):
                status = "401"
                return self._error(401, "unauthorized")
            try:
                payload = await request.json()
            except Exception:
                status = "400"
                return self._error(400, "invalid json body")
            if not isinstance(payload, dict):
                status = "400"
                return self._error(400, "json body must be an object")

            response = await self._handle_payload(payload)
            status = str(response.status)
            return response
        finally:
            HTTP_REQUESTS.labels(
                method="POST", route="/text2img/generate", status_code=status
            ).inc()
            HTTP_REQUEST_DURATION.labels(
                method="POST", route="/text2img/generate"
            ).observe(time.perf_counter() - started)
            self.requests_served += 1
            self.last_request_at = time.time()

    async def _handle_payload(self, payload: dict) -> web.Response:
        html = payload.get("html")
        tmpl = payload.get("tmpl")
        tmplname = payload.get("tmplname")
        tmpldata = payload.get("tmpldata") or {}
        want_json = bool(payload.get("json"))
        raw_options = payload.get("options")

        if tmplname and not tmpl:
            return self._error(
                400, "tmplname 不受支持：插件模式请直接传 tmpl 字符串"
            )
        if not html and not tmpl:
            return self._error(400, "html or tmpl not found")
        if raw_options is not None and not isinstance(raw_options, dict):
            return self._error(400, "options must be an object")

        try:
            options = self._build_options(raw_options)
        except ValidationError as exc:
            return self._error(400, f"invalid options: {exc.errors()[:1]}")

        # 1) 本地渲染
        if self._local_render_available():
            try:
                if tmpl:
                    source = "template"
                    RENDER_INPUT_BYTES.labels(source=source).observe(
                        len(str(tmpl).encode("utf-8"))
                    )
                    _, abs_path = await self.renderer.from_jinja_template(
                        tmpl, tmpldata
                    )
                else:
                    RENDER_INPUT_BYTES.labels(source="html").observe(
                        len(str(html).encode("utf-8"))
                    )
                    _, abs_path = await self.renderer.from_html(html)

                picture = await self.renderer.html2pic(abs_path, options)
                return await self._success_response(picture, want_json)
            except SecurityError as exc:
                return self._error(400, f"security error: {exc}")
            except PlaywrightNotInstalledError as exc:
                self.last_error = str(exc)
                logger.warning(f"[t2i] {exc}")
            except Exception as exc:
                self.last_error = f"本地渲染失败：{exc}"
                logger.warning(f"[t2i] {self.last_error}", exc_info=True)

        # 2) 本地不可用 → 备用端点
        if self.config.server.fallback_enabled:
            return await self._forward_to_fallback(payload)
        return self._error(503, self.last_error or "本地渲染不可用")

    async def _success_response(self, picture: str, want_json: bool) -> web.Response:
        media_type = "image/png" if picture.endswith(".png") else "image/jpeg"
        if want_json:
            image_id = await self.storage.save(picture)
            return web.json_response(
                {"code": 0, "message": "success", "data": {"id": f"data/{image_id}"}}
            )
        return web.FileResponse(picture, headers={"Content-Type": media_type})

    # ------------------------------------------------------------- 测试渲染

    async def render_probe(self, html: str) -> tuple[bool, str]:
        """同步渲染一段 HTML，用于 /t2i_test 与状态页自检。"""
        try:
            _, abs_path = await self.renderer.from_html(html)
            picture = await self.renderer.html2pic(
                abs_path,
                ScreenshotOptions(type="png", full_page=True),
            )
            RENDER_REQUESTS.labels(result="success", format="png", scale="normal").inc()
            return True, picture
        except PlaywrightNotInstalledError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001 - 需要把原因回给用户
            return False, f"渲染失败：{exc}"

    async def ensure_started(self) -> tuple[bool, str]:
        if self.running:
            return True, self.endpoint
        return await self.start()

    async def restart(self) -> tuple[bool, str]:
        await self.stop()
        await asyncio.sleep(0)
        return await self.start()
