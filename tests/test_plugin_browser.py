"""BrowserManager 的检测逻辑：不依赖真实 Chromium，也不许误杀别人的进程。"""

from __future__ import annotations

from pathlib import Path

import pytest

from t2i_plugin import browser as browser_module
from t2i_plugin.browser import BrowserManager


def _make_chromium(browsers_path: Path) -> Path:
    chromium = browsers_path / "chromium-1248" / "chrome-linux64" / "chrome"
    chromium.parent.mkdir(parents=True, exist_ok=True)
    chromium.write_bytes(b"#!/bin/sh\n")
    chromium.chmod(0o755)
    return chromium


def test_find_chromium_uses_explicit_executable_path(tmp_path: Path):
    explicit = tmp_path / "my-chrome"
    explicit.write_bytes(b"#!/bin/sh\n")
    manager = BrowserManager(
        tmp_path / "browsers", executable_path=str(explicit)
    )
    assert manager.find_chromium() == explicit
    assert manager.is_installed() is True


def test_find_chromium_scans_playwright_layout(tmp_path: Path):
    browsers_path = tmp_path / "browsers"
    chromium = _make_chromium(browsers_path)
    manager = BrowserManager(browsers_path)
    assert manager.find_chromium() == chromium
    assert manager.is_installed() is True


def test_find_chromium_returns_none_when_missing(tmp_path: Path):
    manager = BrowserManager(tmp_path / "empty")
    assert manager.find_chromium() is None
    assert manager.is_installed() is False


def test_status_exposes_install_and_process_info(tmp_path: Path, monkeypatch):
    manager = BrowserManager(tmp_path / "browsers")
    monkeypatch.setattr(manager, "chromium_processes", lambda: [{"rss": 1024 * 1024}])
    status = manager.status()
    assert status["installed"] is False
    assert status["processes"] == 1
    assert status["process_memory_mb"] == 1.0
    assert status["install"]["status"] == browser_module.IDLE
    assert status["browsers_path"] == str(tmp_path / "browsers")


def test_install_env_sets_playwright_browsers_path(tmp_path: Path):
    manager = BrowserManager(tmp_path / "browsers", download_host="https://mirror")
    env = manager._install_env()
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "browsers")
    assert env["PLAYWRIGHT_DOWNLOAD_HOST"] == "https://mirror"


def test_download_host_effective_falls_back_to_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_DOWNLOAD_HOST", raising=False)
    manager = BrowserManager(tmp_path / "browsers", download_host="")
    assert manager.download_host_effective() == ""
    monkeypatch.setenv("PLAYWRIGHT_DOWNLOAD_HOST", "https://cdn.example")
    assert manager.download_host_effective() == "https://cdn.example"
    # 配置项优先于环境变量
    manager = BrowserManager(tmp_path / "browsers", download_host="https://cfg.example")
    assert manager.download_host_effective() == "https://cfg.example"


def test_cleanup_orphans_ignores_unrelated_processes(tmp_path: Path):
    """清理逻辑只针对自己 browsers_path 下的进程，绝不能误杀。"""
    manager = BrowserManager(tmp_path / "browsers")
    assert manager.cleanup_orphans() == 0


def test_playwright_pip_available_reflects_environment(monkeypatch):
    monkeypatch.setattr(
        browser_module.importlib.util, "find_spec", lambda name: None
    )
    assert browser_module.playwright_pip_available() is False
    monkeypatch.setattr(
        browser_module.importlib.util, "find_spec", lambda name: object()
    )
    assert browser_module.playwright_pip_available() is True


def test_install_state_tracks_progress():
    state = browser_module.InstallState()
    assert state.running is False
    assert state.as_dict()["status"] == browser_module.IDLE
    state.status = browser_module.RUNNING
    state.started_at = 100.0
    state.finished_at = None
    state.message = "downloading"
    state.log_tail.append("line")
    state.log_tail.append("line2")
    assert state.running is True
    assert state.elapsed > 0
    payload = state.as_dict()
    assert payload["message"] == "downloading"
    assert payload["log_tail"] == ["line", "line2"]


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["/opt/browsers/chromium-1248/chrome-linux64/chrome", "--headless"], True),
        (["/usr/bin/python3", "main.py"], False),
        (["/opt/browsers/chromium-1248/chrome-linux64/chrome"], True),
        (["/usr/lib/chromium/chrome"], False),
        ([], False),
    ],
)
def test_is_chromium_process_only_matches_network_browser(
    argv: list[str], expected: bool
):
    assert browser_module._is_chromium_process(argv, "/opt/browsers") is expected
