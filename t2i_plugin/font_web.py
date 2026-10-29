"""渲染字体页面的后端 Web API（AstrBot Plugin View 桥）。

页面本身是 ``views/fonts/index.html``，通过 ``window.AstrBotPluginView`` 调用
这里的接口。AstrBot 要求注册的路由带插件名前缀，而页面里的 endpoint 不带，
两者不一致是这套桥的既定约定。

旧版 AstrBot 没有 ``astrbot.api.web`` 与 ``register_web_api``：那种情况下
这里直接跳过注册并留一条日志，字体目录、配置项与字体预览指令仍然可用。
"""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Awaitable, Callable

from t2i_plugin.fonts import FontManager

try:
    from astrbot.api import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger(__name__)

try:
    from astrbot.api.web import (
        PluginUploadFile,
        error_response,
        json_response,
        request,
    )

    WEB_API_AVAILABLE = True
except Exception:  # noqa: BLE001
    # 老版本 AstrBot：没有统一的 Web API 门面。页面因此不可用，但插件要能加载。
    PluginUploadFile = None  # type: ignore[assignment]
    error_response = None  # type: ignore[assignment]
    json_response = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]
    WEB_API_AVAILABLE = False


UPLOAD_TMP_DIR = ".upload-tmp"


class FontWebApi:
    """把 FontManager 暴露成页面可调用的 REST 接口。"""

    def __init__(
        self,
        plugin_name: str,
        manager: FontManager,
        *,
        enabled: bool = True,
        specimen: Callable[[list[str], str, str, str], Awaitable[tuple[bool, str]]]
        | None = None,
    ) -> None:
        self.plugin_name = plugin_name
        self.manager = manager
        self.enabled = enabled
        self.specimen = specimen
        self.registered: list[str] = []

    # ------------------------------------------------------------ 注册

    def register(self, context: object) -> list[str]:
        """注册全部路由；返回注册成功的路径，便于状态里展示。"""
        register = getattr(context, "register_web_api", None)
        if not WEB_API_AVAILABLE or not callable(register):
            logger.warning(
                "[t2i] 当前 AstrBot 版本不支持插件页面 Web API，"
                "渲染字体页面不可用（字体目录与配置项仍然有效）"
            )
            return []
        routes: list[tuple[str, Callable[[], Awaitable[object]], list[str], str]] = [
            (f"/{self.plugin_name}/fonts/list", self.page_list, ["GET"], "字体清单"),
            (f"/{self.plugin_name}/fonts/job", self.page_job, ["GET"], "字体任务进度"),
            (f"/{self.plugin_name}/fonts/download", self.page_download, ["POST"], "下载字体"),
            (
                f"/{self.plugin_name}/fonts/download-url",
                self.page_download_url,
                ["POST"],
                "按链接下载字体",
            ),
            (f"/{self.plugin_name}/fonts/upload", self.page_upload, ["POST"], "上传字体"),
            (f"/{self.plugin_name}/fonts/delete", self.page_delete, ["POST"], "删除字体"),
            (f"/{self.plugin_name}/fonts/refresh", self.page_refresh, ["POST"], "刷新字体配置"),
            (f"/{self.plugin_name}/fonts/specimen", self.page_specimen, ["POST"], "字体预览"),
        ]
        for route, handler, methods, desc in routes:
            try:
                register(route, handler, methods, desc)
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响其它路由
                logger.warning(f"[t2i] 注册字体接口失败 {route}：{exc}")
                continue
            self.registered.append(route)
        logger.info(f"[t2i] 渲染字体接口已注册 {len(self.registered)} 条")
        return list(self.registered)

    # ------------------------------------------------------------ 响应工具

    def _ok(self, payload: dict[str, object]) -> object:
        if json_response is None:  # pragma: no cover - 注册阶段已拦下
            return payload
        return json_response(payload)

    def _fail(self, message: str, status_code: int = 400) -> object:
        if error_response is None:  # pragma: no cover
            return {"status": "error", "message": message}
        return error_response(message, status_code=status_code)

    # ------------------------------------------------------------ 接口实现

    async def page_list(self) -> object:
        try:
            catalog = self.manager.catalog()
            status = self.manager.status()
        except Exception as exc:  # noqa: BLE001 - 页面不该因为磁盘异常白屏
            logger.warning(f"[t2i] 读取字体清单失败：{exc}")
            return self._fail(f"读取字体清单失败：{exc}", status_code=500)
        return self._ok(
            {
                "enabled": self.enabled,
                "status": status,
                "catalog": catalog,
                "installed": self.manager.list_installed(),
                "page_api": True,
            }
        )

    async def page_job(self) -> object:
        job = self.manager.job
        return self._ok(
            {
                "job": job.as_dict() if job else None,
                "installed": self.manager.list_installed(),
                "catalog": {
                    "families": [
                        {"key": item["key"], "installed": item["installed"]}
                        for item in self.manager.catalog()["families"]
                    ]
                },
            }
        )

    async def page_download(self) -> object:
        payload = await request.json(default={})
        template = str(payload.get("template") or "").strip()
        keys = payload.get("keys") or []
        if not isinstance(keys, list):
            return self._fail("keys 必须是数组")
        try:
            if template:
                job = self.manager.start_template(template)
            else:
                job = self.manager.start_families([str(key) for key in keys])
        except (ValueError, RuntimeError) as exc:
            return self._fail(str(exc))
        return self._ok({"job": job.as_dict()})

    async def page_download_url(self) -> object:
        payload = await request.json(default={})
        url = str(payload.get("url") or "")
        filename = str(payload.get("filename") or "")
        try:
            job = self.manager.start_url(url, filename)
        except (ValueError, RuntimeError) as exc:
            return self._fail(str(exc))
        return self._ok({"job": job.as_dict()})

    async def page_upload(self) -> object:
        if PluginUploadFile is None:  # pragma: no cover
            return self._fail("当前 AstrBot 版本不支持上传", status_code=500)
        files = await request.files()
        upload = files.get("file")
        if not isinstance(upload, PluginUploadFile):
            return self._fail("没有收到文件（字段名必须是 file）")
        temp_dir = self.manager.fonts_dir / UPLOAD_TMP_DIR
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp = temp_dir / f"upload-{int(time.time() * 1000)}.part"
        try:
            await upload.save(temp)
            data = await asyncio.to_thread(temp.read_bytes)
        except OSError as exc:
            return self._fail(f"读取上传文件失败：{exc}", status_code=500)
        finally:
            temp.unlink(missing_ok=True)
        try:
            info = await self.manager.install_upload(upload.filename or "", data)
        except ValueError as exc:
            return self._fail(str(exc))
        return self._ok({"file": info})

    async def page_delete(self) -> object:
        payload = await request.json(default={})
        try:
            removed = await asyncio.to_thread(
                self.manager.remove, str(payload.get("path") or "")
            )
        except ValueError as exc:
            return self._fail(str(exc))
        except OSError as exc:
            return self._fail(f"删除失败：{exc}", status_code=500)
        await self.manager.after_change()
        return self._ok({"removed": removed, "status": self.manager.status()})

    async def page_refresh(self) -> object:
        """重写字体配置并让渲染侧重新加载（手工改过字体目录后用）。"""
        await self.manager.after_change()
        return self._ok(
            {
                "config_path": str(self.manager.config_path),
                "status": self.manager.status(),
            }
        )

    async def page_specimen(self) -> object:
        """渲染一张字体预览图，验证字体在 Chromium 里确实生效。"""
        if self.specimen is None:
            return self._fail("当前插件实例没有提供渲染能力", status_code=500)
        payload = await request.json(default={})
        families = payload.get("families")
        names = (
            [str(item) for item in families if str(item).strip()]
            if isinstance(families, list)
            else []
        )
        if not names:
            names = _installed_family_names(self.manager)
        raw_scale = payload.get("scale")
        scale = raw_scale if raw_scale in SPECIMEN_SCALES else "normal"
        raw_text = payload.get("text")
        text = raw_text.strip()[:SPECIMEN_TEXT_LIMIT] if isinstance(raw_text, str) else ""
        raw_weights = payload.get("weights")
        weights = raw_weights if raw_weights in SPECIMEN_WEIGHTS else "both"
        try:
            ok, detail = await self.specimen(names, scale, text, weights)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[t2i] 渲染字体预览失败：{exc}")
            return self._fail(f"渲染失败：{exc}", status_code=500)
        if not ok:
            return self._fail(f"渲染失败：{detail}", status_code=500)
        data = await asyncio.to_thread(_read_b64, detail)
        if not data:
            return self._fail("渲染结果读取失败", status_code=500)
        return self._ok(
            {
                "image": f"data:image/png;base64,{data}",
                "families": names,
                "scale": scale,
                "text": text,
                "weights": weights,
            }
        )


# 预览图的像素比等级，与渲染器的 device_scale_factor_level 取值一致
SPECIMEN_SCALES = ("normal", "high", "ultra")

# 每个家族要画哪几种字重的行：两者都画 / 只画常规 / 只画粗体
SPECIMEN_WEIGHTS = ("both", "regular", "bold")

# 自定义预览文字的字符上限：预览图宽度有限，太长会挤成一片
SPECIMEN_TEXT_LIMIT = 120


def _installed_family_names(manager: FontManager) -> list[str]:
    names: list[str] = []
    for item in manager.list_installed():
        for name in item.get("families", []):
            text = str(name)
            if text and text not in names:
                names.append(text)
    return names[:24]


def _read_b64(path: str) -> str:
    try:
        return base64.b64encode(Path(path).read_bytes()).decode("ascii")
    except OSError as exc:
        logger.warning(f"[t2i] 读取预览图失败：{exc}")
        return ""
