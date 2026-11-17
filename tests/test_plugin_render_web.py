"""面板「出图清晰度」接口：读当前值、保存并立即生效、试拍样张。"""

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
    """只传清晰度时，质量下限要沿用当前值，不能被重置成默认。"""
    monkeypatch.setattr(render_web, "request", _FakeRequest({"device_scale_factor": 2.5}))
    saved: list[tuple[float, int, int]] = []

    async def fake_save(factor, quality_floor, default_quality):
        saved.append((factor, quality_floor, default_quality))
        return {
            "device_scale_factor": factor,
            "quality_floor": quality_floor,
            "default_quality": default_quality,
        }

    api = RenderWebApi(
        "astrbot_plugin_t2i_service",
        state=lambda: {
            "device_scale_factor": 2.0,
            "quality_floor": 85,
            "default_quality": 55,
            "default_type": "jpeg",
        },
        save=fake_save,
        preview=lambda *args: None,  # type: ignore[arg-type]
    )
    payload = _payload(await api.page_save())
    assert payload["device_scale_factor"] == 2.5
    assert saved == [(2.5, 85, 55)]


async def test_preview_returns_data_url_and_size(monkeypatch, tmp_path):
    image = tmp_path / "shot.jpg"
    image.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(
        render_web, "request", _FakeRequest({"device_scale_factor": 1.5, "quality": 60})
    )
    calls: list[tuple[float, int]] = []

    async def fake_preview(factor, quality):
        calls.append((factor, quality))
        return True, str(image)

    api = RenderWebApi(
        "astrbot_plugin_t2i_service",
        state=dict,
        save=lambda *args: None,  # type: ignore[arg-type]
        preview=fake_preview,
    )
    payload = _payload(await api.page_preview())
    assert calls == [(1.5, 60)]
    assert payload["image"].startswith("data:image/jpeg;base64,")
    assert payload["quality"] == 60
    assert payload["bytes"] == 4


async def test_preview_failure_is_reported(monkeypatch):
    monkeypatch.setattr(render_web, "request", _FakeRequest({}))

    async def fake_preview(factor, quality):
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
