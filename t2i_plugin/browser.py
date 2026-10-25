"""Chromium / Playwright 的检测、安装与进程管理。

AstrBot 官方镜像里只有 Python 3.12，没有 Chromium；插件不能假设浏览器已就绪。
本模块负责：

* 判断 Python 包 ``playwright`` 与 Chromium 二进制是否存在；
* 在后台执行 ``playwright install chromium``（可配置镜像源，流式记录日志）；
* 清理上一次插件重载/崩溃后残留的孤儿 Chromium 进程（父进程 = 1），避免
  内存一路涨上去；
* 为状态命令 / WebUI 提供可读的运行状态。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from astrbot.api import logger
except Exception:  # noqa: BLE001
    # 两种情况都会走到这里：单测环境里没有 astrbot；
    # 或者 astrbot 在导入时初始化数据目录而当前工作目录不可写（如只读挂载）。
    # 插件真正运行时 cwd 是 AstrBot 根目录，不会触发。
    import logging

    logger = logging.getLogger(__name__)


IDLE = "idle"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


def playwright_pip_available() -> bool:
    """Python 包 playwright 是否已安装。"""
    return importlib.util.find_spec("playwright") is not None


def _proc_entries() -> list[Path]:
    try:
        return [p for p in Path("/proc").iterdir() if p.name.isdigit()]
    except OSError:  # pragma: no cover - 非 Linux
        return []


def _read_proc(pid_dir: Path) -> tuple[int, list[str], int] | None:
    """返回 (ppid, argv, rss_bytes)，进程已消失时返回 None。"""
    try:
        cmdline = (pid_dir / "cmdline").read_bytes().split(b"\0")
        argv = [part.decode("utf-8", "replace") for part in cmdline if part]
        status = {}
        for line in (pid_dir / "status").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            key, separator, value = line.partition(":")
            if separator:
                status[key] = value.strip()
        ppid = int(status.get("PPid", "0").split()[0])
        rss = 0
        statm = (pid_dir / "statm").read_text(encoding="ascii").split()
        if len(statm) > 1:
            rss = int(statm[1]) * os.sysconf("SC_PAGE_SIZE")
        return ppid, argv, rss
    except (OSError, ValueError, IndexError):
        return None


def _is_chromium_process(argv: list[str], browsers_path: str) -> bool:
    if not argv:
        return False
    joined = " ".join(argv)
    if browsers_path and browsers_path not in joined:
        return False
    return "chrome" in joined or "headless_shell" in joined


@dataclass
class InstallState:
    """一次浏览器安装任务的状态。"""

    status: str = IDLE
    message: str = "尚未执行安装"
    started_at: float = 0.0
    finished_at: float = 0.0
    log_tail: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.status == RUNNING

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return end - self.started_at

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "elapsed": round(self.elapsed, 1),
            "log_tail": self.log_tail[-12:],
        }


class BrowserManager:
    """管理插件私有的 Chromium 安装目录与安装任务。"""

    def __init__(
        self,
        browsers_path: Path,
        download_host: str = "",
        executable_path: str = "",
        install_timeout_seconds: int = 900,
    ) -> None:
        self.browsers_path = Path(browsers_path)
        self.download_host = download_host
        self.executable_path = executable_path
        self.install_timeout_seconds = install_timeout_seconds
        self.install_state = InstallState()
        self._install_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 检测

    def find_chromium(self) -> Path | None:
        """定位可用的 Chromium 可执行文件。"""
        if self.executable_path:
            candidate = Path(self.executable_path)
            return candidate if candidate.is_file() else None

        if not self.browsers_path.is_dir():
            return None

        patterns = (
            "chromium-*/chrome-linux/chrome",
            "chromium-*/chrome-linux64/chrome",
            "chromium_headless_shell-*/chrome-linux/headless_shell",
            "chromium_headless_shell-*/chrome-linux64/headless_shell",
        )
        for pattern in patterns:
            matches = sorted(self.browsers_path.glob(pattern))
            for match in reversed(matches):
                if match.is_file() and os.access(match, os.X_OK):
                    return match
        return None

    def is_installed(self) -> bool:
        return self.find_chromium() is not None

    def download_host_effective(self) -> str:
        if self.download_host:
            return self.download_host
        return os.getenv("PLAYWRIGHT_DOWNLOAD_HOST", "")

    def chromium_processes(self) -> list[dict]:
        """扫描属于本插件安装目录的 Chromium 进程。"""
        processes: list[dict] = []
        for entry in _proc_entries():
            info = _read_proc(entry)
            if info is None:
                continue
            ppid, argv, rss = info
            if _is_chromium_process(argv, str(self.browsers_path)):
                processes.append({"pid": int(entry.name), "ppid": ppid, "rss": rss})
        return processes

    def status(self) -> dict:
        pipelines = self.chromium_processes()
        return {
            "pip_playwright": playwright_pip_available(),
            "browsers_path": str(self.browsers_path),
            "chromium_path": str(self.find_chromium() or ""),
            "installed": self.is_installed(),
            "download_host": self.download_host_effective(),
            "processes": len(pipelines),
            "process_memory_mb": round(
                sum(process["rss"] for process in pipelines) / 1024 / 1024, 1
            ),
            "install": self.install_state.as_dict(),
        }

    # ------------------------------------------------------------------ 安装

    def _install_env(self) -> dict:
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(self.browsers_path)
        host = self.download_host_effective()
        if host:
            env["PLAYWRIGHT_DOWNLOAD_HOST"] = host
        return env

    def _append_log(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        self.install_state.log_tail.append(line)
        if len(self.install_state.log_tail) > 60:
            del self.install_state.log_tail[:-60]
        logger.info(f"[t2i-install] {line}")

    async def _run_command(self, args: list[str], env: dict) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdout is not None
        output: list[str] = []
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace")
            self._append_log(line)
            output.append(line)
        return await process.wait(), "".join(output)

    async def install(self, install_deps: bool = False) -> tuple[bool, str]:
        """安装 Chromium（可选同时安装系统依赖）。返回 (成功?, 说明)。"""
        if not playwright_pip_available():
            return False, (
                "未安装 Python 包 playwright：请重装本插件让 AstrBot 自动安装依赖，"
                "或在 AstrBot 环境中执行 `pip install playwright`。"
            )
        if self.install_state.running:
            return False, "已有安装任务在进行中"

        self.install_state = InstallState(
            status=RUNNING, message="正在安装 Chromium…", started_at=time.time()
        )
        env = self._install_env()
        self.browsers_path.mkdir(parents=True, exist_ok=True)

        try:
            if install_deps and shutil.which("apt-get"):
                self.install_state.message = "正在安装 Chromium 系统依赖…"
                code, _ = await asyncio.wait_for(
                    self._run_command(
                        [sys.executable, "-m", "playwright", "install-deps", "chromium"],
                        env,
                    ),
                    timeout=self.install_timeout_seconds,
                )
                if code != 0:
                    self._append_log(
                        "install-deps 失败（缺少 root 权限或 apt 源不可用），"
                        "继续尝试安装浏览器本体"
                    )

            self.install_state.message = "正在下载 Chromium…"
            code, output = await asyncio.wait_for(
                self._run_command(
                    [sys.executable, "-m", "playwright", "install", "chromium"],
                    env,
                ),
                timeout=self.install_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self.install_state.status = FAILED
            self.install_state.finished_at = time.time()
            self.install_state.message = (
                f"安装超时（>{self.install_timeout_seconds}s），请检查网络或镜像设置"
            )
            return False, self.install_state.message
        except FileNotFoundError as exc:  # pragma: no cover - 环境异常
            self.install_state.status = FAILED
            self.install_state.finished_at = time.time()
            self.install_state.message = f"无法启动安装进程：{exc}"
            return False, self.install_state.message

        self.install_state.finished_at = time.time()
        if code == 0 and self.is_installed():
            self.install_state.status = DONE
            self.install_state.message = (
                f"Chromium 安装完成，用时 {self.install_state.elapsed:.0f}s"
            )
            logger.info(self.install_state.message)
            return True, self.install_state.message

        self.install_state.status = FAILED
        tail = " | ".join(self.install_state.log_tail[-3:]) or "无输出"
        if "error while loading shared libraries" in output or "error while loading" in output:
            self.install_state.message = (
                "Chromium 已下载但缺少系统库，请在容器内执行 "
                "`playwright install-deps chromium`（或在插件配置打开 auto_install_deps）。"
            )
        else:
            self.install_state.message = f"安装失败（退出码 {code}）：{tail}"
        return False, self.install_state.message

    def start_install(self, install_deps: bool = False) -> bool:
        """后台启动安装任务；已在运行则返回 False。"""
        if self.install_state.running:
            return False
        self._install_task = asyncio.create_task(self.install(install_deps=install_deps))
        return True

    async def wait_install(self) -> None:
        if self._install_task is not None:
            try:
                await self._install_task
            finally:
                self._install_task = None

    def cancel_install(self) -> None:
        if self._install_task is not None and not self._install_task.done():
            self._install_task.cancel()
        self._install_task = None

    # -------------------------------------------------------------- 孤儿清理

    def cleanup_orphans(self) -> int:
        """杀掉父进程为 1 的残留 Chromium（上一次热重载/崩溃留下的）。"""
        killed = 0
        for process in self.chromium_processes():
            if process["ppid"] != 1:
                continue
            try:
                os.kill(process["pid"], 9)
                killed += 1
            except OSError:
                continue
        if killed:
            logger.info(f"已清理 {killed} 个残留 Chromium 进程")
        return killed
