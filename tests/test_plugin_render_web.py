"""面板「出图清晰度」接口：读当前值、保存并立即生效、测试出图。"""

from __future__ import annotations

import json

from t2i_plugin import render_web
from t2i_plugin.config import (
    MAX_DEVICE_SCALE_FACTOR,
    MAX_QUALITY,
    MIN_DEVICE_SCALE_FACTOR,
    MIN_QUALITY,
    T2IConfig,
    clamp_device_scale_factor,
    clamp_quality,
)
from t2i_plugin.render_web import RenderWebApi


class _FakeContext:
    def __init__(self) -> None:
        self.routes: list[tuple[str, object, list[str], str]] = []

    def register_web_api(self, route, handler, methods, desc) -> None:
        self.routes.append((route, handler, methods, desc))


def _payload(result) -> dict:
    """接口在 AstrBot 里返回 JSONResponse，在裸环境下返回 dict；两种都拆成 dict。"""
    if isinstance(result, dict):
        return result
    body = getattr(result, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return json.loads(bytes(body).decode("utf-8"))
    if isinstance(body, str):
        return json.loads(body)
    raise AssertionError(f"看不懂的响应类型：{type(result)!r}")


class _FakeRequest:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {}

    async def json(self, default=None):
        return self.payload


def test_render_web_registers_prefixed_routes():
    api = RenderWebApi(
        "astrbot_plugin_t2i_service",
        state=dict,
        save=lambda *args: None,  # type: ignore[arg-type]
        preview=lambda *args: None,  # type: ignore[arg-type]
    )
    context = _FakeContext()
    routes = api.register(context)
    assert len(routes) == len(context.routes) == 3
    for route, handler, methods, desc in context.routes:
        assert route.startswith("/astrbot_plugin_t2i_service/render/")
        assert methods in (["GET"], ["POST"])
        assert desc
        assert callable(handler)


def test_render_web_skips_registration_without_web_api():
    api = RenderWebApi(
        "astrbot_plugin_t2i_service",
        state=dict,
        save=lambda *args: None,  # type: ignore[arg-type]
        preview=lambda *args: None,  # type: ignore[arg-type]
    )
    assert api.register(object()) == []
    assert api.registered == []


def test_quality_and_scale_are_clamped_to_ranges():
    assert clamp_quality(120) == MAX_QUALITY
    assert clamp_quality(-5) == MIN_QUALITY
    assert clamp_quality("abc") == 70
    assert clamp_device_scale_factor(9) == MAX_DEVICE_SCALE_FACTOR
    assert clamp_device_scale_factor(0) == MIN_DEVICE_SCALE_FACTOR


async def test_save_only_touches_given_fields(monkeypatch):
    """只传清晰度时，其余字段要沿用当前值，不能被重置成默认。"""
    monkeypatch.setattr(render_web, "request", _FakeRequest({"device_scale_factor": 2.5}))
    saved: list[tuple] = []

    async def fake_save(factor, quality_floor, default_quality, default_type, full_page):
        saved.append((factor, quality_floor, default_quality, default_type, full_page))
        return {
            "device_scale_factor": factor,
            "quality_floor": quality_floor,
            "default_quality": default_quality,
            "default_type": default_type,
            "full_page": full_page,
        }

    api = RenderWebApi(
        "astrbot_plugin_t2i_service",
        state=lambda: {
            "device_scale_factor": 2.0,
            "quality_floor": 85,
            "default_quality": 55,
            "default_type": "png",
            "full_page": False,
        },
        save=fake_save,
        preview=lambda *args, **kwargs: None,  # type: ignore[arg-type]
    )
    payload = _payload(await api.page_save())
    assert payload["device_scale_factor"] == 2.5
    # 格式与整页开关没传，要保持原值而不是被默认值覆盖
    assert saved == [(2.5, 85, 55, "png", False)]


async def test_save_writes_format_and_full_page(monkeypatch):
    """格式与整页开关都要能存下来：AstrBot 固定发 jpeg，格式只能靠面板改。"""
    monkeypatch.setattr(
        render_web,
        "request",
        _FakeRequest({"default_type": "PNG", "full_page": False}),
    )
    saved: list[tuple] = []

    async def fake_save(factor, quality_floor, default_quality, default_type, full_page):
        saved.append((default_type, full_page))
        return {"default_type": default_type, "full_page": full_page}

    api = RenderWebApi(
        "astrbot_plugin_t2i_service",
        state=lambda: {
            "device_scale_factor": 2.0,
            "quality_floor": 70,
            "default_quality": 70,
            "default_type": "jpeg",
            "full_page": True,
        },
        save=fake_save,
        preview=lambda *args, **kwargs: None,  # type: ignore[arg-type]
    )
    _payload(await api.page_save())
    # 大小写要归一化，别把 "PNG" 直接写进配置
    assert saved == [("png", False)]


def _png_bytes(width: int, height: int) -> bytes:
    """最小合法 PNG 头：只要 IHDR 里的宽高，测试不依赖图像库。"""
    header = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
    return header + width.to_bytes(4, "big") + height.to_bytes(4, "big")


async def test_preview_returns_data_url_size_and_dimensions(monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(_png_bytes(1600, 1440))
    monkeypatch.setattr(
        render_web,
        "request",
        _FakeRequest(
            {"device_scale_factor": 2.0, "default_type": "png", "full_page": True}
        ),
    )
    calls: list[tuple] = []

    async def fake_preview(factor, quality, **kwargs):
        calls.append((factor, quality, kwargs))
        return True, str(image)

    api = RenderWebApi(
        "astrbot_plugin_t2i_service",
        state=lambda: {"default_type": "png", "full_page": True},
        save=lambda *args: None,  # type: ignore[arg-type]
        preview=fake_preview,
    )
    payload = _payload(await api.page_preview())
    assert calls[0][0] == 2.0
    assert calls[0][2] == {"shot_type": "png", "full_page": True}
    # png 无损：质量不该再回给页面，否则界面会显示一个不起作用的数字
    assert payload["quality"] is None
    assert payload["image"].startswith("data:image/png;base64,")
    assert (payload["width"], payload["height"]) == (1600, 1440)


async def test_preview_honours_requested_format_and_quality(monkeypatch, tmp_path):
    """jpeg 要能试质量，png 要能试无损：格式必须能透到渲染参数。"""
    image = tmp_path / "shot.jpg"
    image.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(
        render_web,
        "request",
        _FakeRequest({"device_scale_factor": 1.5, "quality": 60, "default_type": "jpeg"}),
    )
    calls: list[tuple] = []

    async def fake_preview(factor, quality, **kwargs):
        calls.append((factor, quality, kwargs))
        return True, str(image)

    api = RenderWebApi(
        "astrbot_plugin_t2i_service",
        state=lambda: {"default_type": "jpeg", "full_page": True},
        save=lambda *args: None,  # type: ignore[arg-type]
        preview=fake_preview,
    )
    payload = _payload(await api.page_preview())
    assert calls == [(1.5, 60, {"shot_type": "jpeg", "full_page": True})]
    assert payload["image"].startswith("data:image/jpeg;base64,")
    assert payload["quality"] == 60
    assert payload["bytes"] == 4


async def test_preview_failure_is_reported(monkeypatch):
    monkeypatch.setattr(render_web, "request", _FakeRequest({}))

    async def fake_preview(factor, quality, **kwargs):
        return False, "浏览器没装"

    api = RenderWebApi(
        "astrbot_plugin_t2i_service",
        state=dict,
        save=lambda *args: None,  # type: ignore[arg-type]
        preview=fake_preview,
    )
    payload = _payload(await api.page_preview())
    assert payload["status"] == "error"
    assert "浏览器没装" in payload["message"]


def test_render_settings_state_exposes_ranges():
    """面板靠 ranges 设滑杆边界，缺了就只能按写死的范围显示。"""
    config = T2IConfig.from_raw({"render": {"device_scale_factor": 2.5}})
    assert config.render.device_scale_factor == 2.5
