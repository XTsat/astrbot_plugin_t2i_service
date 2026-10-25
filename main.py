"""AstrBot 插件入口：在 AstrBot 进程内提供文转图（text2image）渲染服务。

本插件把上游独立的 ``astrbot-t2i-service``（FastAPI + Playwright）移植进
AstrBot 插件进程，用 aiohttp 暴露**完全一致**的 HTTP 契约，因此 AstrBot 主程序
只需要把 ``t2i_endpoint`` 指向本插件地址即可完成文转图渲染：

* 无需再单独部署一个容器 / 占用一个对外服务端口；
* Chromium 由插件自己管理（可选镜像下载、状态检测、孤儿进程清理）；
* 本机浏览器不可用时，可自动透传到上游公共端点，保证「装上就能用」。

命令：
    /t2i_status   查看服务/浏览器/端点状态
    /t2i_install  安装 Chromium（后台执行，带进度）
    /t2i_test     渲染一张测试卡片，验证链路是否打通
    /t2i_restart  重启内嵌 HTTP 服务并释放 Chromium

WebUI 页面：AstrBot 插件页 →「文转图服务」。
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time
from pathlib import Path

# 插件目录入 sys.path，保证 t2i_plugin / t2i_core 可导入（AstrBot 以包路径
# data.plugins.<plugin>.main 加载本文件，子模块不一定在 sys.path 中）。
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from astrbot.api import AstrBotConfig, logger  # noqa: E402
from astrbot.api.event import AstrMessageEvent, filter  # noqa: E402
from astrbot.api.star import Context, Star  # noqa: E402
from astrbot.api.web import error_response, json_response  # noqa: E402
from astrbot.core.utils.astrbot_path import (  # noqa: E402
    get_astrbot_plugin_data_path,
)

from t2i_core.render import Text2ImgRender  # noqa: E402
from t2i_core.util import cleanup_expired_files  # noqa: E402
from t2i_plugin.browser import BrowserManager, playwright_pip_available  # noqa: E402
from t2i_plugin.card import build_status_card, build_test_card  # noqa: E402
from t2i_plugin.config import T2IConfig  # noqa: E402
from t2i_plugin.server import T2IHttpServer  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_t2i_service"
PLUGIN_VERSION = "0.1.0"

INSTALL_PROGRESS_INTERVAL = 20  # 安装进度提示间隔（秒）
INSTALL_PROGRESS_MAX = 8  # 安装进度提示条数上限
INSTALL_POLL_SECONDS = 3
CLEANUP_TICK_SECONDS = 60


class T2IServicePlugin(Star):
    """文转图服务：在 AstrBot 进程内把 HTML / Jinja2 模板渲染成图片。

    Chromium 由插件自己管理（可配置镜像下载、状态检测、孤儿进程清理），
    并把 AstrBot 主程序的文转图端点直接指向本进程内的内嵌服务，
    因此不需要再单独部署 astrbot-t2i-service 容器。

    命令：
      /t2i_status   查看服务 / 浏览器 / 端点状态
      /t2i_install  安装 Chromium（后台执行，带进度）
      /t2i_test     渲染一张测试卡片，验证链路是否打通
      /t2i_restart  重启内嵌 HTTP 服务并释放 Chromium

    注：AstrBot v3.5.19 之后插件类会被自动识别，无需 @register 装饰器；
    名称/版本/描述来自 metadata.yaml。
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.context = context
        self._raw_config = config if config is not None else {}
        self.settings = T2IConfig.from_raw(dict(self._raw_config))

        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.images_dir = self.data_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)

        browsers_path = (
            self.settings.browser.browsers_path or str(self.data_dir / "browsers")
        )
        self.browser_manager = BrowserManager(
            Path(browsers_path),
            download_host=self.settings.browser.download_host,
            executable_path=self.settings.browser.executable_path,
            install_timeout_seconds=self.settings.browser.install_timeout_seconds,
        )

        self.renderer = Text2ImgRender(
            data_dir=str(self.images_dir),
            browsers_path=browsers_path,
            no_sandbox=self.settings.render.no_sandbox,
            concurrency=self.settings.render.concurrency,
            executable_path=self.settings.browser.executable_path or None,
        )

        self.server = T2IHttpServer(
            config=self.settings,
            renderer=self.renderer,
            data_dir=str(self.images_dir),
            browser_manager=self.browser_manager,
        )

        self._closing = False
        self._background_tasks: set[asyncio.Task] = set()
        self._cleanup_task: asyncio.Task | None = None
        self._web_apis_registered = False
        self.loaded_at = time.time()

    # ------------------------------------------------------------ 生命周期

    async def initialize(self) -> None:
        """插件启用时：注册 Web API、清理孤儿进程、启动内嵌服务。"""
        logger.info(f"[t2i] 插件初始化：数据目录 {self.data_dir}")
        try:
            self._register_web_apis()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 注册 Web API 失败：{exc}")

        if self.settings.browser.cleanup_orphans:
            try:
                self.browser_manager.cleanup_orphans()
            except Exception as exc:  # pragma: no cover - 防御
                logger.warning(f"[t2i] 清理残留 Chromium 失败：{exc}")

        if self.settings.server.enabled:
            ok, message = await self.server.start()
            if not ok:
                logger.error(f"[t2i] 内嵌渲染服务启动失败：{message}")
            else:
                await self._apply_endpoint_config()
                if self.settings.render.prelaunch:
                    self._spawn(self._prelaunch_browser())
        else:
            logger.info("[t2i] 内嵌渲染服务未启用（server.enabled = false）")

        self._cleanup_task = asyncio.create_task(self._periodic_maintenance())

    async def terminate(self) -> None:
        """插件停用/重载时：停服务、关浏览器、撤销后台任务。"""
        self._closing = True
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        self.browser_manager.cancel_install()
        try:
            await self.server.stop()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 停止内嵌服务失败：{exc}")
        try:
            await self.renderer.terminate()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 关闭 Chromium 失败：{exc}")
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        logger.info("[t2i] 插件已卸载")

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _prelaunch_browser(self) -> None:
        if self._closing:
            return
        if not playwright_pip_available() or not self.browser_manager.is_installed():
            return
        ok, detail = await self.server.render_probe(build_test_card(quiet=True))
        if ok:
            logger.info("[t2i] 预热浏览器完成")
        else:
            logger.warning(f"[t2i] 预热浏览器失败：{detail}")

    async def _apply_endpoint_config(self) -> None:
        """把 AstrBot 主程序的 t2i 端点指向本插件（可选）。"""
        if not self.settings.endpoint.auto_configure:
            return
        endpoint = self.settings.resolve_service_url()
        try:
            core_config = self.context.get_config()
            core_config["t2i_endpoint"] = endpoint
            core_config["t2i_strategy"] = "remote"
            await _save_config(core_config)
            logger.info(f"[t2i] 已把 AstrBot 文转图端点设置为 {endpoint}")
        except Exception as exc:
            logger.warning(
                f"[t2i] 写入 AstrBot 文转图端点失败（可在 WebUI 手动设置）：{exc}"
            )

    async def _periodic_maintenance(self) -> None:
        """周期性清理过期图片 + 释放闲置 Chromium。"""
        while not self._closing:
            await asyncio.sleep(CLEANUP_TICK_SECONDS)
            if self._closing:
                break
            try:
                lifetime = self.settings.storage.image_lifetime_hours * 3600
                await asyncio.to_thread(
                    cleanup_expired_files, str(self.images_dir), lifetime
                )
            except Exception as exc:  # pragma: no cover - 防御
                logger.debug(f"[t2i] 清理过期图片失败：{exc}")
            try:
                await self.renderer.close_idle(
                    self.settings.render.idle_timeout_seconds
                )
            except Exception as exc:  # pragma: no cover - 防御
                logger.debug(f"[t2i] 释放闲置 Chromium 失败：{exc}")

    # -------------------------------------------------------------- 状态信息

    def _status_payload(self) -> dict:
        return {
            "plugin_name": PLUGIN_NAME,
            "plugin_version": PLUGIN_VERSION,
            "plugin_uptime": round(time.time() - self.loaded_at, 1),
            "server": {
                "enabled": self.settings.server.enabled,
                "running": self.server.running,
                "endpoint": self.server.endpoint,
                "requests": self.server.requests_served,
                "fallback_used": self.server.fallback_used,
                "last_error": self.server.last_error,
            },
            "browser": self.browser_manager.status(),
            "renderer": {
                "connected": self.renderer.is_browser_connected(),
                "concurrency": self.settings.render.concurrency,
                "idle_timeout": self.settings.render.idle_timeout_seconds,
            },
            "endpoint": {
                "auto_configure": self.settings.endpoint.auto_configure,
                "target": self.settings.resolve_service_url(),
                "astrbot_t2i_endpoint": self._read_core_config("t2i_endpoint"),
                "astrbot_t2i_strategy": self._read_core_config("t2i_strategy"),
            },
        }

    def _read_core_config(self, key: str) -> str:
        try:
            return str(self.context.get_config().get(key, ""))
        except Exception:  # pragma: no cover - 防御
            return ""

    def _status_text(self) -> str:
        payload = self._status_payload()
        server = payload["server"]
        browser = payload["browser"]
        renderer = payload["renderer"]
        endpoint = payload["endpoint"]

        if browser["installed"]:
            chromium = f"已安装（{Path(browser['chromium_path']).name}）"
        else:
            chromium = "未安装"
        lines = [
            "【文转图服务状态】",
            f"内嵌服务：{'运行中' if server['running'] else '未运行'}"
            f"（{server['endpoint']}）",
            f"累计请求：{server['requests']} 次；透传备用端点 {server['fallback_used']} 次",
            f"Python playwright：{'已安装' if browser['pip_playwright'] else '未安装'}",
            f"Chromium：{chromium}",
            f"浏览器进程：{browser['processes']} 个，占用 {browser['process_memory_mb']} MB",
            f"浏览器连接：{'已连接' if renderer['connected'] else '空闲/未启动'}",
            f"插件浏览器目录：{browser['browsers_path']}",
            f"安装任务：{browser['install']['status']} - {browser['install']['message']}",
            f"AstrBot 文转图端点：{endpoint['astrbot_t2i_endpoint'] or '（未设置）'}"
            f"（策略：{endpoint['astrbot_t2i_strategy'] or '未知'}）",
        ]
        if server["last_error"]:
            lines.append(f"最近错误：{server['last_error']}")
        if not browser["pip_playwright"]:
            lines.append("提示：重装插件可让 AstrBot 自动安装 playwright 依赖")
        elif not browser["installed"]:
            lines.append("提示：发送 /t2i_install 安装 Chromium（约 1GB 下载）")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 命令

    @filter.command("t2i_status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看文转图服务与 Chromium 状态。"""
        yield event.plain_result(self._status_text())

    @filter.command("t2i_install")
    async def cmd_install(self, event: AstrMessageEvent):
        """安装 Chromium 浏览器（后台下载，带进度提示）。"""
        if not playwright_pip_available():
            yield event.plain_result(
                "未检测到 Python 包 playwright。请先在 AstrBot 中重装本插件"
                "（会自动安装 requirements.txt），或在 AstrBot 环境执行 "
                "`pip install playwright`。"
            )
            return
        if self.browser_manager.install_state.running:
            yield event.plain_result("已有安装任务在进行中，可用 /t2i_status 查看进度。")
            return
        if self.browser_manager.is_installed():
            yield event.plain_result(
                f"Chromium 已安装：{self.browser_manager.find_chromium()}\n"
                "如需强制重装，请删除插件数据目录下的 browsers 文件夹后重试。"
            )
            return

        self.browser_manager.start_install(
            install_deps=self.settings.browser.auto_install_deps
        )
        yield event.plain_result(
            "已开始安装 Chromium…\n"
            f"下载源：{self.browser_manager.download_host_effective() or '默认 CDN'}\n"
            f"安装目录：{self.browser_manager.browsers_path}"
        )

        sent = 0
        waited = 0
        limit = self.settings.browser.install_timeout_seconds
        while self.browser_manager.install_state.running and waited < limit:
            await asyncio.sleep(INSTALL_POLL_SECONDS)
            waited += INSTALL_POLL_SECONDS
            state = self.browser_manager.install_state
            if not state.running:
                break
            if waited % INSTALL_PROGRESS_INTERVAL != 0 or sent >= INSTALL_PROGRESS_MAX:
                continue
            sent += 1
            tail = "\n".join(state.log_tail[-2:])
            yield event.plain_result(
                f"安装中…已用时 {state.elapsed:.0f}s｜{state.message}"
                + (f"\n{tail}" if tail else "")
            )

        state = self.browser_manager.install_state
        if state.status == "done":
            yield event.plain_result(
                f"✅ {state.message}\n浏览器路径：{self.browser_manager.find_chromium()}\n"
                "可以发送 /t2i_test 验证渲染效果。"
            )
            await self._apply_endpoint_config()
        else:
            yield event.plain_result(f"❌ 安装未成功：{state.message}")

    @filter.command("t2i_test")
    async def cmd_test(self, event: AstrMessageEvent):
        """渲染一张测试卡片，验证文转图链路是否正常。"""
        payload = self._status_payload()
        html = build_test_card(
            quiet=False,
            server_running=payload["server"]["running"],
            endpoint=payload["server"]["endpoint"],
            chromium_installed=payload["browser"]["installed"],
            chromium_path=payload["browser"]["chromium_path"],
            renderer_connected=payload["renderer"]["connected"],
        )
        started = time.perf_counter()
        ok, detail = await self.server.render_probe(html)
        elapsed = time.perf_counter() - started
        if ok:
            yield event.image_result(detail)
            yield event.plain_result(f"渲染成功，用时 {elapsed * 1000:.0f} ms")
        else:
            yield event.plain_result(f"渲染失败：{detail}")

    @filter.command("t2i_restart")
    async def cmd_restart(self, event: AstrMessageEvent):
        """重启内嵌服务并释放 Chromium。"""
        try:
            await self.renderer.terminate()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 关闭 Chromium 失败：{exc}")
        if not self.settings.server.enabled:
            yield event.plain_result(
                "内嵌服务未启用（server.enabled = false），已释放 Chromium。"
            )
            return
        ok, message = await self.server.restart()
        if ok:
            yield event.plain_result(f"✅ 内嵌服务已重启：{self.server.endpoint}")
        else:
            yield event.plain_result(f"❌ 重启失败：{message}")

    # ------------------------------------------------------------- Web API

    def _register_web_apis(self) -> None:
        if self._web_apis_registered:
            return
        base = f"/{PLUGIN_NAME}"
        self.context.register_web_api(
            f"{base}/status", self.web_status, ["GET"], "文转图服务状态"
        )
        self.context.register_web_api(
            f"{base}/install", self.web_install, ["POST"], "安装 Chromium"
        )
        self.context.register_web_api(
            f"{base}/install/state",
            self.web_install_state,
            ["GET"],
            "Chromium 安装进度",
        )
        self.context.register_web_api(
            f"{base}/render-test", self.web_render_test, ["POST"], "渲染测试卡片"
        )
        self.context.register_web_api(
            f"{base}/apply-endpoint",
            self.web_apply_endpoint,
            ["POST"],
            "让 AstrBot 主程序使用本插件端点",
        )
        self.context.register_web_api(
            f"{base}/restart", self.web_restart, ["POST"], "重启内嵌服务"
        )
        self.context.register_web_api(
            f"{base}/status-card", self.web_status_card, ["GET"], "状态卡片图片"
        )
        self._web_apis_registered = True

    async def web_status(self):
        return json_response({"status": "ok", "data": self._status_payload()})

    async def web_install(self):
        if not playwright_pip_available():
            return error_response(
                "未安装 Python 包 playwright，请重装插件以自动安装依赖", status_code=400
            )
        chromium = self.browser_manager.find_chromium()
        if chromium is not None:
            return json_response(
                {
                    "status": "ok",
                    "data": {"already_installed": True, "chromium_path": str(chromium)},
                }
            )
        started = self.browser_manager.start_install(
            install_deps=self.settings.browser.auto_install_deps
        )
        return json_response({"status": "ok", "data": {"started": started}})

    async def web_install_state(self):
        return json_response(
            {
                "status": "ok",
                "data": {
                    **self.browser_manager.install_state.as_dict(),
                    "installed": self.browser_manager.is_installed(),
                },
            }
        )

    async def web_render_test(self):
        payload = self._status_payload()
        html = build_test_card(
            quiet=False,
            server_running=payload["server"]["running"],
            endpoint=payload["server"]["endpoint"],
            chromium_installed=payload["browser"]["installed"],
            chromium_path=payload["browser"]["chromium_path"],
            renderer_connected=payload["renderer"]["connected"],
        )
        started = time.perf_counter()
        ok, detail = await self.server.render_probe(html)
        elapsed = time.perf_counter() - started
        if not ok:
            return json_response(
                {"status": "error", "message": detail, "data": {"ok": False}}
            )
        image = _read_as_data_url(detail)
        if image is None:
            return error_response("读取渲染结果失败", status_code=500)
        return json_response(
            {
                "status": "ok",
                "data": {
                    "ok": True,
                    "elapsed_ms": round(elapsed * 1000, 1),
                    "image": image,
                },
            }
        )

    async def web_apply_endpoint(self):
        try:
            core_config = self.context.get_config()
            endpoint = self.settings.resolve_service_url()
            core_config["t2i_endpoint"] = endpoint
            core_config["t2i_strategy"] = "remote"
            await _save_config(core_config)
        except Exception as exc:
            return error_response(f"写入失败：{exc}", status_code=500)
        return json_response(
            {
                "status": "ok",
                "data": {
                    "endpoint": endpoint,
                    "strategy": "remote",
                    "note": "若 AstrBot 已缓存文转图策略，重启 AstrBot 后生效",
                },
            }
        )

    async def web_restart(self):
        try:
            await self.renderer.terminate()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 关闭 Chromium 失败：{exc}")
        if not self.settings.server.enabled:
            return json_response(
                {"status": "ok", "data": {"enabled": False, "running": False}}
            )
        ok, message = await self.server.restart()
        return json_response(
            {
                "status": "ok" if ok else "error",
                "message": message,
                "data": {
                    "running": self.server.running,
                    "endpoint": self.server.endpoint,
                },
            }
        )

    async def web_status_card(self):
        ok, detail = await self.server.render_probe(
            build_status_card(self._status_payload())
        )
        if not ok:
            return error_response(detail, status_code=503)
        image = _read_as_data_url(detail)
        if image is None:
            return error_response("读取渲染结果失败", status_code=500)
        return json_response({"status": "ok", "data": {"image": image}})



async def _save_config(core_config) -> None:
    """持久化 AstrBot 配置：新版有 async 版本，旧版回退到同步写入。"""
    save_async = getattr(core_config, "save_config_async", None)
    if save_async is not None:
        await save_async()
    else:  # pragma: no cover - 旧版 AstrBot 兼容
        core_config.save_config()


def _read_as_data_url(path: str) -> str | None:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    suffix = Path(path).suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(raw).decode()
