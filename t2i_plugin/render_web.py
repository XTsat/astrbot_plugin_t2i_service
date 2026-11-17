"""文转图服务面板「出图清晰度」栏的后端 Web API。

页面里直接把清晰度（设备像素比）与 JPEG 质量调成具体数字，而不是只给几档
预设：糊不糊取决于手机端实际放大倍数，只有连续可调才能试到自己满意。

三个接口：

* ``render/settings``（GET） —— 当前生效的格式、质量、清晰度与可调范围；
* ``render/settings``（POST）—— 保存清晰度与质量，立即对后续出图生效；
* ``render/preview``（POST）—— 按给定参数试拍一张样张，返回 data URL 与尺寸，
  方便在同一屏里对比「1 倍 / 2 倍」「质量 40 / 80」的差别。
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from pathlib import Path

from astrbot.api import logger

from t2i_plugin.config import (
    MAX_DEVICE_SCALE_FACTOR,
    MAX_QUALITY,
    MIN_DEVICE_SCALE_FACTOR,
    MIN_QUALITY,
    clamp_device_scale_factor,
    clamp_quality,
)

try:
    from astrbot.api.web import error_response, json_response, request

    WEB_API_AVAILABLE = True
except Exception:  # noqa: BLE001
    # 老版本 AstrBot：没有统一的 Web API 门面。页面因此不可用，但插件要能加载。
    error_response = None  # type: ignore[assignment]
    json_response = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]
    WEB_API_AVAILABLE = False


class RenderWebApi:
    """把「出图清晰度」的读写与试拍暴露成页面可调用的接口。"""

    def __init__(
        self,
        plugin_name: str,
        *,
        state: Callable[[], dict[str, object]],
        save: Callable[[float, int, int], Awaitable[dict[str, object]]],
        preview: Callable[[float, int], Awaitable[tuple[bool, str]]],
    ) -> None:
        self.plugin_name = plugin_name
        self.state = state
        self.save = save
        self.preview = preview
        self.registered: list[str] = []

    # ------------------------------------------------------------ 注册

    def register(self, context: object) -> list[str]:
        """注册路由；返回注册成功的路径，便于状态里展示。"""
        register = getattr(context, "register_web_api", None)
        if not WEB_API_AVAILABLE or not callable(register):
            logger.warning("[t2i] 当前 AstrBot 版本不支持插件页面 Web API，清晰度面板不可用")
            return []
        routes: list[tuple[str, Callable[[], Awaitable[object]], list[str], str]] = [
            (
                f"/{self.plugin_name}/render/settings",
                self.page_settings,
                ["GET"],
                "出图参数",
            ),
            (
                f"/{self.plugin_name}/render/settings",
                self.page_save,
                ["POST"],
                "保存出图参数",
            ),
            (
                f"/{self.plugin_name}/render/preview",
                self.page_preview,
                ["POST"],
                "出图样张",
            ),
        ]
        for route, handler, methods, desc in routes:
            try:
                register(route, handler, methods, desc)
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响其它路由
                logger.warning(f"[t2i] 注册出图参数接口失败 {route}：{exc}")
                continue
            self.registered.append(route)
        logger.info(f"[t2i] 出图参数接口已注册 {len(self.registered)} 条")
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

    async def page_settings(self) -> object:
        return self._ok(self.state())

    async def page_save(self) -> object:
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return self._fail("请求体必须是对象")
        current = self.state()
        # 只传了其中一两项时，其余沿用当前值：页面上改一个滑块不该把别的重置
        raw_factor = payload.get("device_scale_factor", current["device_scale_factor"])
        factor = clamp_device_scale_factor(raw_factor)
        quality_floor = clamp_quality(
            payload.get("quality_floor", current["quality_floor"]),
            int(current["quality_floor"]),
        )
        default_quality = clamp_quality(
            payload.get("default_quality", current["default_quality"]),
            int(current["default_quality"]),
        )
        try:
            saved = await self.save(factor, quality_floor, default_quality)
        except Exception as exc:  # noqa: BLE001 - 保存失败要把原因回给页面
            logger.warning(f"[t2i] 保存出图参数失败：{exc}")
            return self._fail(f"保存失败：{exc}", status_code=500)
        return self._ok(saved)

    async def page_preview(self) -> object:
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return self._fail("请求体必须是对象")
        factor = clamp_device_scale_factor(
            payload.get("device_scale_factor")
        )
        quality = clamp_quality(payload.get("quality"), 70)
        try:
            ok, detail = await self.preview(factor, quality)
        except Exception as exc:  # noqa: BLE001 - 渲染失败要把原因回给页面
            logger.warning(f"[t2i] 渲染出图样张失败：{exc}")
            return self._fail(f"渲染失败：{exc}", status_code=500)
        if not ok:
            return self._fail(f"渲染失败：{detail}", status_code=500)
        data = await asyncio.to_thread(_read_b64, detail)
        if not data:
            return self._fail("样张读取失败", status_code=500)
        return self._ok(
            {
                "image": f"data:image/jpeg;base64,{data}",
                "device_scale_factor": factor,
                "quality": quality,
                "bytes": await asyncio.to_thread(_file_size, detail),
            }
        )


def _read_b64(path: str) -> str:
    try:
        return base64.b64encode(Path(path).read_bytes()).decode("ascii")
    except OSError as exc:
        logger.warning(f"[t2i] 读取样张失败：{exc}")
        return ""


def _file_size(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


__all__ = [
    "MAX_DEVICE_SCALE_FACTOR",
    "MAX_QUALITY",
    "MIN_DEVICE_SCALE_FACTOR",
    "MIN_QUALITY",
    "RenderWebApi",
]
