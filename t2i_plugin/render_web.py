"""文转图服务面板「出图清晰度」栏的后端 Web API。

页面里直接把清晰度（设备像素比）与 JPEG 质量调成具体数字，而不是只给几档
预设：糊不糊取决于手机端实际放大倍数，只有连续可调才能试到自己满意。

三个接口：

* ``render/settings``（GET） —— 当前生效的格式、质量、清晰度与可调范围；
* ``render/settings``（POST）—— 保存清晰度、质量、格式与整页开关，立即对后续出图生效；
* ``render/preview``（POST）—— 按给定参数测试出图，返回 data URL 与像素尺寸，
  方便在同一屏里对比「1 倍 / 2 倍」「质量 40 / 80」「JPEG / PNG」的差别。
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
    coerce_bool,
    coerce_render_type,
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
    """把「出图清晰度」的读写与测试出图暴露成页面可调用的接口。"""

    def __init__(
        self,
        plugin_name: str,
        *,
        state: Callable[[], dict[str, object]],
        save: Callable[..., Awaitable[dict[str, object]]],
        preview: Callable[..., Awaitable[tuple[bool, str]]],
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
        # 面板只有一个「质量」滑块：它同时写默认质量与质量下限，否则两个值打架时
        # 用户看到 80、实际按 70 出图。没传质量时两个字段各自保留原值
        if "quality" in payload:
            quality = clamp_quality(payload.get("quality"), int(current["quality_floor"]))
            quality_floor = default_quality = quality
        else:
            quality_floor = clamp_quality(
                current["quality_floor"], int(current["quality_floor"])
            )
            default_quality = clamp_quality(
                current["default_quality"], int(current["default_quality"])
            )
        render_type = coerce_render_type(
            payload.get("default_type", current.get("default_type"))
        )
        full_page = coerce_bool(
            payload.get("full_page"), bool(current.get("full_page"))
        )
        try:
            saved = await self.save(
                factor, quality_floor, default_quality, render_type, full_page
            )
        except Exception as exc:  # noqa: BLE001 - 保存失败要把原因回给页面
            logger.warning(f"[t2i] 保存出图参数失败：{exc}")
            return self._fail(f"保存失败：{exc}", status_code=500)
        return self._ok(saved)

    async def page_preview(self) -> object:
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return self._fail("请求体必须是对象")
        current = self.state()
        factor = clamp_device_scale_factor(
            payload.get("device_scale_factor", current.get("device_scale_factor"))
        )
        # 质量跟随面板当前值（也就是真实出图会用的那个），不传时才退回当前配置
        quality = clamp_quality(
            payload.get("quality", current.get("quality_floor")), 70
        )
        render_type = coerce_render_type(
            payload.get("default_type", current.get("default_type"))
        )
        full_page = coerce_bool(
            payload.get("full_page", current.get("full_page")), True
        )
        # png 无损，质量参数对它没有意义，回给页面时也别显示成「质量 70」
        shown_quality = None if render_type == "png" else quality
        try:
            ok, detail = await self.preview(
                factor,
                quality,
                shot_type=render_type,
                full_page=full_page,
            )
        except Exception as exc:  # noqa: BLE001 - 渲染失败要把原因回给页面
            logger.warning(f"[t2i] 测试出图失败：{exc}")
            return self._fail(f"渲染失败：{exc}", status_code=500)
        if not ok:
            return self._fail(f"渲染失败：{detail}", status_code=500)
        data = await asyncio.to_thread(_read_b64, detail)
        if not data:
            return self._fail("出图结果读取失败", status_code=500)
        width, height = await asyncio.to_thread(_image_size, detail)
        return self._ok(
            {
                "image": f"data:image/{render_type};base64,{data}",
                "device_scale_factor": factor,
                "default_type": render_type,
                "quality": shown_quality,
                "full_page": full_page,
                "width": width,
                "height": height,
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


def _image_size(path: str) -> tuple[int, int]:
    """读出图片像素宽高，让页面能证明「2 倍」真的生效了。

    只解析 PNG / JPEG 头，不引入图像库：插件运行时不想为了两个数字多一个依赖。
    解析不出来就回 (0, 0)，页面按「尺寸未知」显示。
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                width = int.from_bytes(head[16:20], "big")
                height = int.from_bytes(head[20:24], "big")
                return width, height
            if head[:2] == b"\xff\xd8":
                return _jpeg_size(fh)
    except OSError as exc:
        logger.warning(f"[t2i] 读取图片尺寸失败：{exc}")
    return 0, 0


def _jpeg_size(fh) -> tuple[int, int]:
    """在 JPEG 的 SOF 段里找宽高；段头是 0xFF 0xMN + 两字节长度。"""
    fh.seek(2)
    while True:
        marker = fh.read(2)
        if len(marker) < 2 or marker[0] != 0xFF:
            return 0, 0
        code = marker[1]
        # SOF0..SOF15（除去 DHT/JPG/DAC 三个非 SOF）里带着宽高
        if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
            segment = fh.read(7)
            if len(segment) < 7:
                return 0, 0
            height = int.from_bytes(segment[3:5], "big")
            width = int.from_bytes(segment[5:7], "big")
            return width, height
        length_raw = fh.read(2)
        if len(length_raw) < 2:
            return 0, 0
        length = int.from_bytes(length_raw, "big")
        if length < 2:
            return 0, 0
        fh.seek(length - 2, 1)


__all__ = [
    "MAX_DEVICE_SCALE_FACTOR",
    "MAX_QUALITY",
    "MIN_DEVICE_SCALE_FACTOR",
    "MIN_QUALITY",
    "RenderWebApi",
]
