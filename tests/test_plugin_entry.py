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
import time
from pathlib import Path

import aiohttp
import pytest

import main as plugin_main
from t2i_plugin.browser import DONE, FAILED, RUNNING, InstallState
from t2i_plugin.card import build_status_card, build_test_card
from t2i_plugin.config import DEFAULT_FALLBACK_ENDPOINT


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


# ------------------------------------------------ 配置页「下载 Chromium」开关


class RecordingConfig(dict):
    """模拟 AstrBot 下发的插件配置对象（dict + save_config_async）。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.saves = 0

    async def save_config_async(self, replace_config=None, *, indent: int = 2) -> bool:
        self.saves += 1
        return True


class FakeInstallState:
    def __init__(self, status: str = "idle", message: str = "") -> None:
        self.status = status
        self.message = message

    @property
    def running(self) -> bool:
        return self.status == "running"


class FakeManager:
    """只实现开关处理用到的浏览器管理接口。"""

    def __init__(self, *, installed: bool, install_status: str = "done") -> None:
        self._installed = installed
        self.install_status = install_status
        self.install_state = FakeInstallState()
        self.install_calls: list[bool] = []

    def is_installed(self) -> bool:
        return self._installed

    def start_install(self, install_deps: bool = False) -> bool:
        self.install_calls.append(install_deps)
        self.install_state = FakeInstallState(
            status=self.install_status, message="安装完成" if self.install_status == "done" else "下载失败"
        )
        if self.install_status == "done":
            self._installed = True
        return True

    def download_host_effective(self) -> str:
        return "https://mirror.example"

    def find_chromium(self) -> str:
        return "/fake/chromium"


async def test_download_switch_starts_install_and_closes_itself(plugin):
    """配置页保存开关 → 后台开始下载；结束后开关自动收回，就绪状态写回。"""
    raw = RecordingConfig(
        {
            "server": {"enabled": False, "port": free_port()},
            "render": {"prelaunch": False},
            "browser": {"cleanup_orphans": False, "download_chromium": True},
        }
    )
    instance = plugin(raw)
    manager = FakeManager(installed=False)
    instance.browser_manager = manager

    await instance._handle_download_switch()

    assert manager.install_calls == [instance.settings.browser.auto_install_deps]
    assert raw["browser"]["download_chromium"] is False
    assert raw["browser"]["chromium_ready"] is True
    assert raw.saves >= 1


async def test_download_switch_is_ignored_when_chromium_ready(plugin):
    """本机已有浏览器时不下第二次，开关只被收回。"""
    raw = RecordingConfig(
        {
            "server": {"enabled": False, "port": free_port()},
            "render": {"prelaunch": False},
            "browser": {"cleanup_orphans": False, "download_chromium": True},
        }
    )
    instance = plugin(raw)
    manager = FakeManager(installed=True)
    instance.browser_manager = manager

    await instance._handle_download_switch()

    assert manager.install_calls == []
    assert raw["browser"]["download_chromium"] is False
    assert raw["browser"]["chromium_ready"] is True


async def test_download_switch_reports_failure_by_closing_switch(plugin):
    """下载失败同样收回开关，避免下次重载反复重试。"""
    raw = RecordingConfig(
        {
            "server": {"enabled": False, "port": free_port()},
            "render": {"prelaunch": False},
            "browser": {"cleanup_orphans": False, "download_chromium": True},
        }
    )
    instance = plugin(raw)
    manager = FakeManager(installed=False, install_status="failed")
    instance.browser_manager = manager

    await instance._handle_download_switch()

    assert manager.install_calls != []
    assert raw["browser"]["download_chromium"] is False
    assert raw["browser"]["chromium_ready"] is False


async def test_download_switch_closed_state_does_not_start_install(plugin):
    """开关是关的（默认）时只在状态变化时回写，不触发下载。"""
    raw = RecordingConfig(
        {
            "server": {"enabled": False, "port": free_port()},
            "render": {"prelaunch": False},
            "browser": {"cleanup_orphans": False},
        }
    )
    instance = plugin(raw)
    manager = FakeManager(installed=False)
    instance.browser_manager = manager

    await instance._handle_download_switch()
    assert manager.install_calls == []
    assert "download_chromium" not in raw["browser"]
    assert raw["browser"]["chromium_ready"] is False


async def test_legacy_fallback_endpoint_is_migrated(plugin):
    """老配置里留着已停用的备用端点时自动换成当前默认值。"""
    raw = RecordingConfig(
        {
            "server": {
                "enabled": False,
                "port": free_port(),
                "fallback_endpoint": "https://t2i.soulter.top/text2img",
            },
            "render": {"prelaunch": False},
            "browser": {"cleanup_orphans": False},
        }
    )
    instance = plugin(raw)

    await instance._migrate_legacy_fallback_endpoint()

    assert raw["server"]["fallback_endpoint"] == DEFAULT_FALLBACK_ENDPOINT
    assert instance.settings.server.fallback_endpoint == DEFAULT_FALLBACK_ENDPOINT


async def test_custom_fallback_endpoint_is_kept(plugin):
    """用户自己填的备用端点不被动到。"""
    raw = RecordingConfig(
        {
            "server": {
                "enabled": False,
                "port": free_port(),
                "fallback_endpoint": "http://10.0.0.5:8999/text2img",
            },
            "render": {"prelaunch": False},
            "browser": {"cleanup_orphans": False},
        }
    )
    instance = plugin(raw)

    await instance._migrate_legacy_fallback_endpoint()

    assert raw["server"]["fallback_endpoint"] == "http://10.0.0.5:8999/text2img"
    assert instance.settings.server.fallback_endpoint == "http://10.0.0.5:8999/text2img"


async def test_service_url_field_is_written_back_for_display(plugin):
    """配置页的「本插件当前地址」按监听地址与端口回写，且不重复写盘。"""
    port = free_port()
    raw = RecordingConfig(
        {
            "server": {"enabled": False, "host": "127.0.0.1", "port": port},
            "render": {"prelaunch": False},
            "browser": {"cleanup_orphans": False},
        }
    )
    instance = plugin(raw)

    await instance._sync_service_url_field()
    expected = f"http://127.0.0.1:{port}/text2img"
    assert raw["endpoint"]["current_service_url"] == expected
    assert instance.settings.endpoint.current_service_url == expected
    assert raw.saves == 1

    # 地址没变时不再写配置，避免每次启动都多一次落盘
    await instance._sync_service_url_field()
    assert raw.saves == 1


# ------------------------------------------------- 安装/补装进度与失败提示


def _running_deps(elapsed: float) -> InstallState:
    return InstallState(
        status=RUNNING,
        message="正在安装 Chromium 系统依赖…",
        started_at=time.time() - elapsed,
    )


async def test_install_progress_text_reports_elapsed_and_message(plugin):
    instance = plugin({"server": {"enabled": False, "port": free_port()}})
    instance.browser_manager.deps_state = _running_deps(42)
    text = instance._install_progress_text("deps_state")
    assert "补装系统依赖" in text
    assert "已用时 42s" in text
    assert "正在安装 Chromium 系统依赖…" in text


async def test_install_result_text_covers_failure_and_success(plugin):
    instance = plugin({"server": {"enabled": False, "port": free_port()}})
    failed = InstallState(status=FAILED, message="仍有系统库缺失：libnspr4.so")
    assert instance._install_result_text(failed, "deps_state").startswith("❌")

    done = InstallState(status=DONE, message="系统依赖已补齐，用时 12s")
    text = instance._install_result_text(done, "deps_state")
    assert text.startswith("✅")
    assert "/t2i_test" in text


async def test_install_hint_while_deps_install_is_running(plugin, monkeypatch):
    instance = plugin({"server": {"enabled": False, "port": free_port()}})
    instance.browser_manager.deps_state = _running_deps(7)
    hint = instance._install_hint()
    assert "正在补装" in hint
    assert "已用时 7s" in hint


async def test_install_hint_points_to_install_command_when_libs_missing(
    plugin, monkeypatch
):
    instance = plugin({"server": {"enabled": False, "port": free_port()}})
    manager = instance.browser_manager
    manager.deps_state = InstallState(status=FAILED, message="仍有系统库缺失")
    monkeypatch.setattr(manager, "is_installed", lambda: True)
    monkeypatch.setattr(manager, "missing_shared_libs", lambda: ["libnspr4.so"])
    monkeypatch.setattr(plugin_main, "deps_install_available", lambda: True)
    assert "/t2i_install" in instance._install_hint()

    monkeypatch.setattr(plugin_main, "deps_install_available", lambda: False)
    assert "playwright install-deps chromium" in instance._install_hint()


async def test_install_hint_is_empty_when_nothing_to_fix(plugin, monkeypatch):
    instance = plugin({"server": {"enabled": False, "port": free_port()}})
    manager = instance.browser_manager
    manager.deps_state = InstallState()
    manager.install_state = InstallState()
    monkeypatch.setattr(manager, "is_installed", lambda: True)
    monkeypatch.setattr(manager, "missing_shared_libs", lambda: [])
    assert instance._install_hint() == ""
