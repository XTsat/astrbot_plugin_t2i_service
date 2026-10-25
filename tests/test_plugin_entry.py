"""插件入口（main.py）的装配与生命周期测试。

用假的 AstrBot Context 代替真实事件总线，验证：
* 配置被正确解析进各组件；
* 7 个 Web API 路由被注册；
* initialize() 能真的把内嵌服务拉起来，terminate() 能干净地关掉；
* 状态字典/状态卡片能构造出来。
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import aiohttp
import pytest

import main as plugin_main
from t2i_plugin.card import build_status_card, build_test_card


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakeContext:
    """只实现插件入口真正用到的那几个方法。"""

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self.web_apis: list[tuple[str, object, list[str], str]] = []

    def register_web_api(self, route, view_handler, methods, desc) -> None:
        self.web_apis.append((route, view_handler, list(methods), desc))

    def get_config(self) -> dict:
        return self._config


@pytest.fixture()
def plugin(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        plugin_main, "get_astrbot_plugin_data_path", lambda: str(tmp_path)
    )
    def factory(raw_config: dict) -> plugin_main.T2IServicePlugin:
        return plugin_main.T2IServicePlugin(FakeContext(), raw_config)

    yield factory


async def test_config_is_parsed_into_components(plugin):
    port = free_port()
    instance = plugin(
        {
            "server": {"enabled": False, "host": "127.0.0.1", "port": port},
            "render": {"concurrency": 2, "prelaunch": False},
            "browser": {"download_host": "https://mirror.example"},
        }
    )
    assert instance.settings.server.port == port
    assert instance.settings.render.concurrency == 2
    assert instance.settings.browser.download_host == "https://mirror.example"
    assert instance.images_dir == Path(instance.data_dir) / "data"
    assert instance.data_dir.is_dir()
    assert instance.images_dir.is_dir()


async def test_web_apis_are_registered(plugin):
    port = free_port()
    instance = plugin(
        {"server": {"enabled": False, "port": port}, "render": {"prelaunch": False}}
    )
    instance._register_web_apis()
    routes = {route for route, *_ in instance.context.web_apis}
    base = "/astrbot_plugin_t2i_service"
    assert routes == {
        f"{base}/status",
        f"{base}/install",
        f"{base}/install/state",
        f"{base}/render-test",
        f"{base}/apply-endpoint",
        f"{base}/restart",
        f"{base}/status-card",
    }
    for route, handler, methods, desc in instance.context.web_apis:
        assert callable(handler)
        assert methods and all(m in {"GET", "POST"} for m in methods)
        assert desc


async def test_initialize_starts_embedded_server_and_terminate_stops_it(plugin):
    port = free_port()
    instance = plugin(
        {
            "server": {"enabled": True, "host": "127.0.0.1", "port": port},
            "render": {"prelaunch": False},
            "browser": {"cleanup_orphans": False, "auto_configure": False},
        }
    )
    await instance.initialize()
    try:
        assert instance.server.running is True
        assert instance.server.endpoint.startswith("http://127.0.0.1:")
        health_url = instance.server.endpoint.removesuffix("/text2img") + "/healthz"
        async with aiohttp.ClientSession() as session:
            async with session.get(health_url) as resp:
                assert resp.status == 200
                payload = await resp.json()
        assert payload["status"] == "ok"
        assert payload["service"] == "astrbot_plugin_t2i_service"
        assert payload["endpoint"] == instance.server.endpoint
    finally:
        await instance.terminate()
    assert instance.server.running is False
    assert instance._closing is True


async def test_status_payload_and_cards_render(plugin):
    port = free_port()
    instance = plugin(
        {"server": {"enabled": False, "port": port}, "render": {"prelaunch": False}}
    )
    payload = instance._status_payload()
    json.dumps(payload)  # 必须可被 WebUI 直接序列化
    for key in ("server", "browser", "renderer", "endpoint", "plugin_uptime"):
        assert key in payload

    status_card = build_status_card(payload)
    assert "文转图服务状态" in status_card
    assert "内嵌服务" in status_card

    test_card = build_test_card(
        server_running=True,
        endpoint=instance.server.endpoint,
        chromium_installed=True,
        chromium_path="/opt/chromium/chrome",
        renderer_connected=True,
    )
    assert "文转图服务自检" in test_card
    assert "READY" in test_card
    assert "<script" not in test_card.lower()


async def test_status_text_is_human_readable(plugin):
    port = free_port()
    instance = plugin(
        {"server": {"enabled": False, "port": port}, "render": {"prelaunch": False}}
    )
    text = instance._status_text()
    assert "文转图" in text or "t2i" in text.lower()
    assert str(port) in text
