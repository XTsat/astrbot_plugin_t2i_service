"""Chromium / Playwright 的检测、安装与进程管理。

AstrBot 官方镜像里只有 Python 3.12，没有 Chromium；插件不能假设浏览器已就绪。
本模块负责：

* 判断 Python 包 ``playwright`` 与 Chromium 二进制是否存在；
* 用 ``ldd`` 检查 Chromium 缺哪些系统运行库，并在后台执行
  ``playwright install chromium`` / ``playwright install-deps chromium``
  （可配置镜像源，流式记录日志）；
* 清理上一次插件重载/崩溃后残留的孤儿 Chromium 进程（父进程 = 1），避免
  内存一路涨上去；
* 为状态命令 / WebUI 提供可读的运行状态。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from astrbot.api import logger


IDLE = "idle"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

LDD_TIMEOUT_SECONDS = 20

# Chromium 启动失败时 glibc 打的那句话，例如
# ``chrome-headless-shell: error while loading shared libraries: libnspr4.so``
_LAUNCH_LIB_ERROR = re.compile(r"error while loading shared libraries: (?P<soname>[^\s:]+)")


def playwright_pip_available() -> bool:
    """Python 包 playwright 是否已安装。"""
    return importlib.util.find_spec("playwright") is not None


def parse_missing_shared_libs(ldd_output: str) -> list[str]:
    """从 ``ldd`` 输出里抽出缺少的 soname（去重、保持出现顺序）。

    ``ldd`` 对缺失依赖的行形如 ``\\tlibnspr4.so => not found``；其它行（已解析到
    真实路径、``linux-vdso.so.1`` 等）都带 ``=>`` 但不含 ``not found``，一并跳过。
    """
    missing: list[str] = []
    for line in ldd_output.splitlines():
        soname, separator, target = line.partition("=>")
        if not separator or not target.strip().startswith("not found"):
            continue
        name = soname.strip()
        if name and name not in missing:
            missing.append(name)
    return missing


def explain_launch_error(message: str) -> str:
    """把 Chromium 启动失败翻译成可执行的提示；不是缺库错误时原样返回。

    直接把 Playwright 的原始报错（整段浏览器启动日志）回给用户等于没说，
    这里改成「缺哪个库 + 怎么修」。
    """
    sonames = list(dict.fromkeys(_LAUNCH_LIB_ERROR.findall(message)))
    if not sonames:
        return message
    return (
        f"Chromium 缺少系统运行库：{'、'.join(sonames)}。"
        "浏览器本体已下载，但容器内缺系统依赖；"
        "发送 /t2i_install 会自动补装（需 AstrBot 进程为 root 且 apt-get/dnf 可用）。"
    )


def deps_install_available() -> bool:
    """能否在本机补装系统依赖：需要 root 权限与 apt-get/dnf。"""
    if os.name != "posix" or getattr(os, "geteuid", lambda: -1)() != 0:
        return False
    return bool(shutil.which("apt-get") or shutil.which("dnf"))


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
    # headless shell 的可执行文件叫 chrome-headless-shell（连字符），旧写法只认
    # headless_shell，会漏掉 headless 模式下真正在跑的进程，导致清理不彻底。
    return (
        "chrome" in joined
        or "headless_shell" in joined
        or "headless-shell" in joined
    )


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
        self.deps_state = InstallState()
        self._install_task: asyncio.Task | None = None
        self._deps_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 检测

    def find_chromium(self) -> Path | None:
        """定位可用的 Chromium 可执行文件（headless shell 优先）。

        Playwright 在 ``headless=True`` 下启动的是 headless shell，而不是完整
        Chromium；系统库检查必须对着真正会被启动的那个二进制做，否则会出现
        「完整版能跑、实际渲染用的 headless shell 缺库」的误判。
        """
        if self.executable_path:
            candidate = Path(self.executable_path)
            return candidate if candidate.is_file() else None

        if not self.browsers_path.is_dir():
            return None

        patterns = (
            "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
            "chromium_headless_shell-*/chrome-headless-shell-linux/chrome-headless-shell",
            "chromium_headless_shell-*/chrome-linux64/headless_shell",
            "chromium_headless_shell-*/chrome-linux/headless_shell",
            "chromium-*/chrome-linux64/chrome",
            "chromium-*/chrome-linux/chrome",
        )
        for pattern in patterns:
            matches = sorted(self.browsers_path.glob(pattern))
            for match in reversed(matches):
                if match.is_file() and os.access(match, os.X_OK):
                    return match
        return None

    def is_installed(self) -> bool:
        return self.find_chromium() is not None

    def missing_shared_libs(self) -> list[str]:
        """``ldd`` 检查浏览器可执行文件缺哪些系统库；无法判断时返回空列表。

        实测 ``ldd`` 在 678 MB 的浏览器上约 2 ms，可以直接同步调用；判断不出来
        的情况（非 Linux、没有 ldd、非 ELF）不报错，交给渲染时的错误提示兜底。
        """
        binary = self.find_chromium()
        if binary is None:
            return []
        try:
            completed = subprocess.run(
                ["ldd", str(binary)],
                capture_output=True,
                text=True,
                timeout=LDD_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0 and not completed.stdout:
            return []
        return parse_missing_shared_libs(completed.stdout)

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
        missing_libs = self.missing_shared_libs()
        return {
            "pip_playwright": playwright_pip_available(),
            "browsers_path": str(self.browsers_path),
            "chromium_path": str(self.find_chromium() or ""),
            "installed": self.is_installed(),
            "missing_libs": missing_libs,
            "deps_ready": not missing_libs,
            "deps_installable": deps_install_available(),
            "download_host": self.download_host_effective(),
            "processes": len(pipelines),
            "process_memory_mb": round(
                sum(process["rss"] for process in pipelines) / 1024 / 1024, 1
            ),
            "install": self.install_state.as_dict(),
            "deps": self.deps_state.as_dict(),
        }

    # ------------------------------------------------------------------ 安装

    def _install_env(self) -> dict:
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(self.browsers_path)
        host = self.download_host_effective()
        if host:
            env["PLAYWRIGHT_DOWNLOAD_HOST"] = host
        return env

    def _append_log(self, line: str, state: InstallState | None = None) -> None:
        line = line.strip()
        if not line:
            return
        target = state if state is not None else self.install_state
        target.log_tail.append(line)
        if len(target.log_tail) > 60:
            del target.log_tail[:-60]
        logger.info(f"[t2i-install] {line}")

    async def _run_command(
        self, args: list[str], env: dict, state: InstallState | None = None
    ) -> tuple[int, str]:
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
            self._append_log(line, state)
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
            if install_deps and deps_install_available():
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
            missing = self.missing_shared_libs()
            if not missing:
                self.install_state.status = DONE
                self.install_state.message = (
                    f"Chromium 安装完成，用时 {self.install_state.elapsed:.0f}s"
                )
                logger.info(f"[t2i] {self.install_state.message}")
                return True, self.install_state.message
            # 下载成功不代表能启动：容器里缺系统库时 Chromium 会以退出码 127 起不来，
            # 只有 ldd 才看得出差哪几个库，所以这里单独判一次。
            self.install_state.status = FAILED
            self.install_state.message = (
                f"Chromium 已下载，但缺少系统库：{'、'.join(missing)}。"
                "发送 /t2i_install 可继续补装系统依赖。"
            )
            logger.warning(f"[t2i] {self.install_state.message}")
            return False, self.install_state.message

        self.install_state.status = FAILED
        tail = " | ".join(self.install_state.log_tail[-3:]) or "无输出"
        self.install_state.message = (
            explain_launch_error(output)
            if "error while loading" in output
            else f"安装失败（退出码 {code}）：{tail}"
        )
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
        for attr in ("_install_task", "_deps_task"):
            task: asyncio.Task | None = getattr(self, attr)
            if task is not None and not task.done():
                task.cancel()
            setattr(self, attr, None)

    # -------------------------------------------------------------- 系统依赖

    async def install_deps(self) -> tuple[bool, str]:
        """补装 Chromium 的系统运行库。返回 (成功?, 说明)。

        交给 Playwright 自己的 ``install-deps``：它按发行版维护包名映射（Debian 13
        的 ``t64`` 改名、老版本 Ubuntu 的旧包名都能对上），内部先刷新软件源再只装
        缺失的包；自己维护 soname→包名 表既难维护又容易装错。
        """
        if self.deps_state.running:
            return False, "已有系统依赖安装任务在进行中"
        if not playwright_pip_available():
            return False, "未安装 Python 包 playwright，无法补装系统依赖"
        if self.find_chromium() is None:
            return False, "未找到 Chromium，请先发送 /t2i_install 安装浏览器"
        if not deps_install_available():
            message = (
                "补装系统依赖需要 root 权限与 apt-get/dnf；"
                "请在 AstrBot 容器内手动执行 `playwright install-deps chromium`"
            )
            self.deps_state = InstallState(
                status=FAILED,
                message=message,
                started_at=time.time(),
                finished_at=time.time(),
            )
            return False, message

        self.deps_state = InstallState(
            status=RUNNING, message="正在安装 Chromium 系统依赖…", started_at=time.time()
        )
        env = self._install_env()
        try:
            code, _ = await asyncio.wait_for(
                self._run_command(
                    [sys.executable, "-m", "playwright", "install-deps", "chromium"],
                    env,
                    state=self.deps_state,
                ),
                timeout=self.install_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self.deps_state.status = FAILED
            self.deps_state.finished_at = time.time()
            self.deps_state.message = (
                f"系统依赖安装超时（>{self.install_timeout_seconds}s），请检查软件源"
            )
            return False, self.deps_state.message
        except FileNotFoundError as exc:  # pragma: no cover - 环境异常
            self.deps_state.status = FAILED
            self.deps_state.finished_at = time.time()
            self.deps_state.message = f"无法启动安装进程：{exc}"
            return False, self.deps_state.message

        self.deps_state.finished_at = time.time()
        # 退出码不可全信：安装器可能装了一部分就失败，以 ldd 复查结果为准。
        missing = self.missing_shared_libs()
        if not missing:
            self.deps_state.status = DONE
            self.deps_state.message = (
                f"系统依赖已补齐，用时 {self.deps_state.elapsed:.0f}s"
            )
            logger.info(f"[t2i] {self.deps_state.message}")
            return True, self.deps_state.message

        self.deps_state.status = FAILED
        if code == 0:
            # 退出码 0 但还缺库：说明对应的包「已安装」但文件损坏/被删，
            # 重装依赖不会动它，只能重装包或重装浏览器。
            self.deps_state.message = (
                f"仍有系统库缺失：{'、'.join(missing)}，可能是软件包文件损坏；"
                "可尝试 `apt-get install --reinstall <包名>`，"
                "或删除插件数据目录下的 browsers 文件夹后重新 /t2i_install"
            )
        else:
            tail = " | ".join(self.deps_state.log_tail[-3:]) or "无输出"
            self.deps_state.message = (
                f"仍有系统库缺失：{'、'.join(missing)}（退出码 {code}，{tail}）"
            )
        logger.warning(f"[t2i] {self.deps_state.message}")
        return False, self.deps_state.message

    def start_install_deps(self) -> bool:
        """后台启动系统依赖补装；已在运行则返回 False。"""
        if self.deps_state.running:
            return False
        self._deps_task = asyncio.create_task(self.install_deps())
        return True

    async def wait_install_deps(self) -> None:
        if self._deps_task is not None:
            try:
                await self._deps_task
            finally:
                self._deps_task = None

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
