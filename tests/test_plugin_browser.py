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


# ---------------------------------------------------------------- 系统依赖

LDD_WITH_MISSING = """\
\tlinux-vdso.so.1 (0x00007ffd4a1f6000)
\tlibnspr4.so => not found
\tlibnss3.so => not found
\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f2c9a400000)
\t/lib64/ld-linux-x86-64.so.2 (0x00007f2c9ac00000)
"""


def _make_headless_shell(browsers_path: Path) -> Path:
    shell = (
        browsers_path
        / "chromium_headless_shell-1248"
        / "chrome-headless-shell-linux64"
        / "chrome-headless-shell"
    )
    shell.parent.mkdir(parents=True, exist_ok=True)
    shell.write_bytes(b"#!/bin/sh\n")
    shell.chmod(0o755)
    return shell


def test_parse_missing_shared_libs_keeps_only_not_found():
    assert browser_module.parse_missing_shared_libs(LDD_WITH_MISSING) == [
        "libnspr4.so",
        "libnss3.so",
    ]
    assert browser_module.parse_missing_shared_libs("") == []
    assert browser_module.parse_missing_shared_libs("\tnot a dynamic executable\n") == []


def test_parse_missing_shared_libs_dedupes():
    output = "\tlibnspr4.so => not found\n\tlibnspr4.so => not found\n"
    assert browser_module.parse_missing_shared_libs(output) == ["libnspr4.so"]


def test_explain_launch_error_translates_missing_library():
    raw = (
        "BrowserType.launch: Target page, context or browser has been closed\n"
        "[pid=318][err] /path/chrome-headless-shell: error while loading shared "
        "libraries: libnspr4.so: cannot open shared object file: No such file"
    )
    message = browser_module.explain_launch_error(raw)
    assert "libnspr4.so" in message
    assert "/t2i_install" in message
    assert "Target page" not in message


def test_explain_launch_error_passes_other_errors_through():
    assert browser_module.explain_launch_error("timeout") == "timeout"


def test_find_chromium_prefers_headless_shell(tmp_path: Path):
    """headless 模式启动的是 headless shell，检测/查库必须对着同一个二进制。"""
    browsers_path = tmp_path / "browsers"
    shell = _make_headless_shell(browsers_path)
    _make_chromium(browsers_path)
    manager = BrowserManager(browsers_path)
    assert manager.find_chromium() == shell


def test_missing_shared_libs_reads_ldd_output(tmp_path: Path, monkeypatch):
    browsers_path = tmp_path / "browsers"
    _make_headless_shell(browsers_path)
    manager = BrowserManager(browsers_path)

    def fake_run(args, **kwargs):
        assert args[0] == "ldd"
        return browser_module.subprocess.CompletedProcess(
            args, 0, LDD_WITH_MISSING, ""
        )

    monkeypatch.setattr(browser_module.subprocess, "run", fake_run)
    assert manager.missing_shared_libs() == ["libnspr4.so", "libnss3.so"]


def test_missing_shared_libs_returns_empty_without_browser(tmp_path: Path):
    assert BrowserManager(tmp_path / "empty").missing_shared_libs() == []


def test_deps_install_available_requires_root_and_package_manager(monkeypatch):
    monkeypatch.setattr(browser_module.os, "geteuid", lambda: 1000, raising=False)
    assert browser_module.deps_install_available() is False
    monkeypatch.setattr(browser_module.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        browser_module.shutil, "which", lambda name: None if name == "dnf" else "/bin/apt-get"
    )
    assert browser_module.deps_install_available() is True


async def test_install_deps_installs_then_rechecks(tmp_path: Path, monkeypatch):
    browsers_path = tmp_path / "browsers"
    _make_headless_shell(browsers_path)
    manager = BrowserManager(browsers_path)
    monkeypatch.setattr(browser_module, "playwright_pip_available", lambda: True)
    monkeypatch.setattr(browser_module, "deps_install_available", lambda: True)
    calls: list[list[str]] = []

    async def fake_run(args, env, state=None):
        calls.append(list(args))
        return 0, ""

    monkeypatch.setattr(manager, "_run_command", fake_run)
    monkeypatch.setattr(manager, "missing_shared_libs", lambda: [])

    ok, message = await manager.install_deps()
    assert ok is True
    assert calls[0][-2:] == ["install-deps", "chromium"]
    assert manager.deps_state.status == browser_module.DONE
    assert "补齐" in message


async def test_install_deps_reports_remaining_libs(tmp_path: Path, monkeypatch):
    browsers_path = tmp_path / "browsers"
    _make_headless_shell(browsers_path)
    manager = BrowserManager(browsers_path)
    monkeypatch.setattr(browser_module, "playwright_pip_available", lambda: True)
    monkeypatch.setattr(browser_module, "deps_install_available", lambda: True)

    async def fake_run(args, env, state=None):
        manager._append_log("E: Unable to locate package", state)
        return 100, "E: Unable to locate package"

    monkeypatch.setattr(manager, "_run_command", fake_run)
    monkeypatch.setattr(manager, "missing_shared_libs", lambda: ["libnspr4.so"])

    ok, message = await manager.install_deps()
    assert ok is False
    assert "libnspr4.so" in message
    assert manager.deps_state.status == browser_module.FAILED


async def test_install_deps_flags_broken_package_when_exit_code_is_zero(
    tmp_path: Path, monkeypatch
):
    """退出码 0 但接口复查仍缺库：多半是包文件被删/损坏，提示重装包。"""
    browsers_path = tmp_path / "browsers"
    _make_headless_shell(browsers_path)
    manager = BrowserManager(browsers_path)
    monkeypatch.setattr(browser_module, "playwright_pip_available", lambda: True)
    monkeypatch.setattr(browser_module, "deps_install_available", lambda: True)

    async def fake_run(args, env, state=None):
        manager._append_log("0 upgraded, 0 newly installed, 0 to remove", state)
        return 0, "0 upgraded, 0 newly installed, 0 to remove"

    monkeypatch.setattr(manager, "_run_command", fake_run)
    monkeypatch.setattr(manager, "missing_shared_libs", lambda: ["libnspr4.so"])

    ok, message = await manager.install_deps()
    assert ok is False
    assert "libnspr4.so" in message
    assert "reinstall" in message
    assert manager.deps_state.status == browser_module.FAILED


async def test_install_deps_without_root_explains_manual_step(
    tmp_path: Path, monkeypatch
):
    browsers_path = tmp_path / "browsers"
    _make_headless_shell(browsers_path)
    manager = BrowserManager(browsers_path)
    monkeypatch.setattr(browser_module, "playwright_pip_available", lambda: True)
    monkeypatch.setattr(browser_module, "deps_install_available", lambda: False)

    ok, message = await manager.install_deps()
    assert ok is False
    assert "playwright install-deps chromium" in message
    assert manager.deps_state.status == browser_module.FAILED


async def test_install_deps_needs_browser_first(tmp_path: Path, monkeypatch):
    manager = BrowserManager(tmp_path / "empty")
    monkeypatch.setattr(browser_module, "playwright_pip_available", lambda: True)
    monkeypatch.setattr(browser_module, "deps_install_available", lambda: True)
    ok, message = await manager.install_deps()
    assert ok is False
    assert "先发送 /t2i_install" in message


async def test_install_reports_missing_libs_after_download(
    tmp_path: Path, monkeypatch
):
    """下载成功但缺库时必须给出可操作结论，而不是说「安装完成」。"""
    browsers_path = tmp_path / "browsers"
    shell = _make_headless_shell(browsers_path)
    manager = BrowserManager(browsers_path)
    monkeypatch.setattr(browser_module, "playwright_pip_available", lambda: True)

    async def fake_run(args, env, state=None):
        return 0, ""

    monkeypatch.setattr(manager, "_run_command", fake_run)
    monkeypatch.setattr(manager, "missing_shared_libs", lambda: ["libcu2.so"])

    ok, message = await manager.install(install_deps=False)
    assert ok is False
    assert "libcu2.so" in message
    assert manager.install_state.status == browser_module.FAILED
    assert shell.exists()


def test_status_exposes_deps_state(tmp_path: Path, monkeypatch):
    manager = BrowserManager(tmp_path / "browsers")
    monkeypatch.setattr(manager, "chromium_processes", lambda: [])
    monkeypatch.setattr(manager, "missing_shared_libs", lambda: ["libnspr4.so"])
    status = manager.status()
    assert status["missing_libs"] == ["libnspr4.so"]
    assert status["deps_ready"] is False
    assert status["deps"]["status"] == browser_module.IDLE
