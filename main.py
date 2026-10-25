"""AstrBot 插件入口：在 AstrBot 进程内提供文转图（text2image）渲染服务。

本插件把上游独立的 ``astrbot-t2i-service``（FastAPI + Playwright）移植进
AstrBot 插件进程，用 aiohttp 暴露**完全一致**的 HTTP 契约，因此 AstrBot 主程序
只需要把 ``t2i_endpoint`` 指向本插件地址即可完成文转图渲染：

* 无需再单独部署一个容器 / 占用一个对外服务端口；
* Chromium 由插件自己管理（可选镜像下载、状态检测、孤儿进程清理）；
* 本机浏览器不可用时，可自动透传到上游公共端点，保证「装上就能用」。

命令：
    /t2i_status   查看服务/浏览器/端点状态
    /t2i_install  安装 Chromium；已装但缺系统库时改为补装系统依赖
    /t2i_test     渲染一张测试卡片，验证链路是否打通
    /t2i_restart  重启内嵌 HTTP 服务并释放 Chromium

WebUI 页面：AstrBot 插件页 →「文转图服务」。
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

# 插件目录入 sys.path，保证 t2i_plugin / t2i_core 可导入（AstrBot 以包路径
# data.plugins.<plugin>.main 加载本文件，子模块不一定在 sys.path 中）。
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# AstrBot 重载插件只清理 data.plugins.<plugin>.* 模块，而 t2i_plugin 是按顶层包名
# 导入的，会残留在 sys.modules 里：不先清掉，重载后依旧跑旧代码，新增符号缺失时
# 甚至直接 ImportError 让插件加载失败。
# 只清 t2i_plugin：t2i_core 里是 Prometheus 注册表一类进程级单例，重新执行会产生
# 重复的指标序列。
for _stale in [
    name for name in sys.modules if name.split(".", 1)[0] == "t2i_plugin"
]:
    del sys.modules[_stale]

from astrbot.api import AstrBotConfig, logger  # noqa: E402
from astrbot.api.event import (  # noqa: E402
    AstrMessageEvent,
    MessageEventResult,
    filter,
)
from astrbot.api.star import Context, Star  # noqa: E402
from astrbot.api.web import error_response, json_response  # noqa: E402
from astrbot.core.utils.astrbot_path import (  # noqa: E402
    get_astrbot_plugin_data_path,
)

from t2i_core.render import Text2ImgRender  # noqa: E402
from t2i_core.util import cleanup_expired_files  # noqa: E402
from t2i_plugin.browser import (  # noqa: E402
    DONE,
    BrowserManager,
    InstallState,
    deps_install_available,
    playwright_pip_available,
)
from t2i_plugin.card import build_status_card, build_test_card  # noqa: E402
from t2i_plugin.config import DEFAULT_FALLBACK_ENDPOINT, T2IConfig  # noqa: E402
from t2i_plugin.server import T2IHttpServer  # noqa: E402

# 历史上会写进用户配置的备用端点，官方已停用；值为空时视为「未自定义」
LEGACY_FALLBACK_ENDPOINTS = {
    "https://t2i.soulter.top/text2img",
    "https://t2i.soulter.top",
}

PLUGIN_NAME = "astrbot_plugin_t2i_service"

INSTALL_PROGRESS_INTERVAL = 20  # 安装进度提示间隔（秒）
INSTALL_PROGRESS_MAX = 8  # 安装进度提示条数上限
INSTALL_POLL_SECONDS = 3
CLEANUP_TICK_SECONDS = 60


class T2IServicePlugin(Star):
    """文转图服务：在 AstrBot 进程内把 HTML / Jinja2 模板渲染成图片。

    Chromium 由插件自己管理（可配置镜像下载、状态检测、孤儿进程清理），
    并把 AstrBot 主程序的文转图端点直接指向本进程内的内嵌服务，
    因此不需要再单独部署 astrbot-t2i-service 容器。

    命令：
      /t2i_status   查看服务 / 浏览器 / 端点状态
      /t2i_install  安装 Chromium；已装但缺系统库时改为补装系统依赖
      /t2i_test     渲染一张测试卡片，验证链路是否打通
      /t2i_restart  重启内嵌 HTTP 服务并释放 Chromium

    注：AstrBot v3.5.19 之后插件类会被自动识别，无需 @register 装饰器；
    名称/版本/描述来自 metadata.yaml。
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.context = context
        self._raw_config = config if config is not None else {}
        self.settings = T2IConfig.from_raw(dict(self._raw_config))

        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.images_dir = self.data_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)

        browsers_path = (
            self.settings.browser.browsers_path or str(self.data_dir / "browsers")
        )
        self.browser_manager = BrowserManager(
            Path(browsers_path),
            download_host=self.settings.browser.download_host,
            executable_path=self.settings.browser.executable_path,
            install_timeout_seconds=self.settings.browser.install_timeout_seconds,
        )

        self.renderer = Text2ImgRender(
            data_dir=str(self.images_dir),
            browsers_path=browsers_path,
            no_sandbox=self.settings.render.no_sandbox,
            concurrency=self.settings.render.concurrency,
            executable_path=self.settings.browser.executable_path or None,
        )

        self.server = T2IHttpServer(
            config=self.settings,
            renderer=self.renderer,
            data_dir=str(self.images_dir),
            browser_manager=self.browser_manager,
        )

        self._closing = False
        self._background_tasks: set[asyncio.Task] = set()
        self._cleanup_task: asyncio.Task | None = None
        self._web_apis_registered = False
        self.loaded_at = time.time()

    # ------------------------------------------------------------ 生命周期

    async def initialize(self) -> None:
        """插件启用时：注册 Web API、清理孤儿进程、启动内嵌服务。"""
        logger.info(f"[t2i] 插件初始化：数据目录 {self.data_dir}")
        try:
            self._register_web_apis()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 注册 Web API 失败：{exc}")

        if self.settings.browser.cleanup_orphans:
            try:
                self.browser_manager.cleanup_orphans()
            except Exception as exc:  # pragma: no cover - 防御
                logger.warning(f"[t2i] 清理残留 Chromium 失败：{exc}")

        self._schedule_deps_install()

        if self.settings.server.enabled:
            ok, message = await self.server.start()
            if not ok:
                logger.error(f"[t2i] 内嵌渲染服务启动失败：{message}")
            else:
                await self._apply_endpoint_config()
                if self.settings.render.prelaunch:
                    self._spawn(self._prelaunch_browser())
        else:
            logger.info("[t2i] 内嵌渲染服务未启用（server.enabled = false）")

        await self._migrate_legacy_fallback_endpoint()
        await self._sync_service_url_field()
        self._spawn(self._handle_download_switch())
        self._cleanup_task = asyncio.create_task(self._periodic_maintenance())

    async def terminate(self) -> None:
        """插件停用/重载时：停服务、关浏览器、撤销后台任务。"""
        self._closing = True
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        self.browser_manager.cancel_install()
        try:
            await self.server.stop()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 停止内嵌服务失败：{exc}")
        try:
            await self.renderer.terminate()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 关闭 Chromium 失败：{exc}")
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        logger.info("[t2i] 插件已卸载")

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _schedule_deps_install(self) -> None:
        """启动时若浏览器缺系统库且允许自动补装，就在后台补一次。

        缺库的表现是「浏览器明明装好了，一渲染就崩」，用户通常猜不到是系统依赖
        问题，所以一旦检测到缺库就顺手补上，而不是等渲染失败再排查。
        """
        if not self.settings.browser.auto_install_deps:
            return
        manager = self.browser_manager
        missing = manager.missing_shared_libs()
        if not missing:
            return
        if not deps_install_available():
            logger.warning(
                f"[t2i] Chromium 缺少系统库 {'、'.join(missing)}，"
                "但当前环境无法自动补装（需要 root 与 apt-get/dnf）"
            )
            return
        if manager.start_install_deps():
            logger.warning(
                f"[t2i] Chromium 缺少系统库 {'、'.join(missing)}，已在后台自动补装"
            )

    async def _migrate_legacy_fallback_endpoint(self) -> None:
        """把老配置里写死的停用端点换成当前默认值。

        旧默认端点已无可用后端，升级上来的用户配置里仍留着它，透传必然失败；
        只有原值确实等于老默认值时才改，用户自己填的地址一律不动。
        """
        current = self._raw_config.get("server", {}) if isinstance(self._raw_config, dict) else {}
        stored = current.get("fallback_endpoint") if isinstance(current, dict) else None
        if not isinstance(stored, str) or stored.strip() not in LEGACY_FALLBACK_ENDPOINTS:
            return
        logger.info(
            f"[t2i] 备用端点 {stored} 已停用，自动切换为 {DEFAULT_FALLBACK_ENDPOINT}"
        )
        self.settings.server.fallback_endpoint = DEFAULT_FALLBACK_ENDPOINT
        await self._update_plugin_config(
            {"server": {"fallback_endpoint": DEFAULT_FALLBACK_ENDPOINT}}
        )

    async def _sync_service_url_field(self) -> None:
        """把实际生效的端点地址回写到配置页的「本插件当前地址」。

        「写入的端点地址」默认留空时，用户在配置页看不到插件到底会写入什么，
        只能自己按 host/port 拼；这里把算好的地址显式回写，方便直接抄进
        AstrBot 的「文本转图像服务 API 地址」。该字段只用于展示。
        """
        url = self.settings.resolve_service_url()
        self.settings.endpoint.current_service_url = url
        await self._update_plugin_config({"endpoint": {"current_service_url": url}})

    async def _handle_download_switch(self) -> None:
        """处理配置页的「下载 Chromium」开关。

        配置页只有开关 + 保存，没有按钮，所以把开关当一次性按钮用：读到 ``True``
        就启动下载，下载结束（成功或失败）再把开关写回 ``False``；同时按本地实际
        状态维护 ``chromium_ready``，配置页用它显示浏览器是否已就绪。
        """
        try:
            await self._sync_download_switch_state()
        except Exception as exc:  # pragma: no cover - 后台任务不能把异常抛给事件循环
            logger.warning(f"[t2i] 处理下载开关失败：{exc}")

    async def _sync_download_switch_state(self) -> None:
        installed = self.browser_manager.is_installed()
        await self._update_plugin_config({"browser": {"chromium_ready": installed}})
        if not self.settings.browser.download_chromium:
            return
        if installed:
            logger.info("[t2i] 下载开关已打开，但本地 Chromium 已就绪，只关闭开关")
            await self._update_plugin_config({"browser": {"download_chromium": False}})
            return
        if not playwright_pip_available():
            logger.warning(
                "[t2i] 下载开关已打开，但缺少 Python 包 playwright，无法下载浏览器"
            )
            await self._update_plugin_config({"browser": {"download_chromium": False}})
            return
        if self.browser_manager.install_state.running:
            logger.info("[t2i] 已有安装任务在进行，忽略配置页的下载请求")
            return
        if not self.browser_manager.start_install(
            install_deps=self.settings.browser.auto_install_deps
        ):
            logger.warning("[t2i] 配置页触发的 Chromium 下载未能启动")
            await self._update_plugin_config({"browser": {"download_chromium": False}})
            return

        logger.info(
            "[t2i] 已按配置页开关开始下载 Chromium："
            f"{self.browser_manager.download_host_effective() or '默认 CDN'}"
        )
        limit = self.settings.browser.install_timeout_seconds
        waited = 0
        while (
            self.browser_manager.install_state.running
            and waited < limit
            and not self._closing
        ):
            await asyncio.sleep(INSTALL_POLL_SECONDS)
            waited += INSTALL_POLL_SECONDS
        if self._closing:
            return

        state = self.browser_manager.install_state
        done = state.status == DONE
        await self._update_plugin_config(
            {
                "browser": {
                    "download_chromium": False,
                    "chromium_ready": self.browser_manager.is_installed(),
                }
            }
        )
        if done:
            logger.info(
                f"[t2i] 配置页触发的 Chromium 下载完成：{self.browser_manager.find_chromium()}"
            )
            await self._apply_endpoint_config()
        else:
            logger.warning(f"[t2i] 配置页触发的 Chromium 下载未成功：{state.message}")

    async def _update_plugin_config(
        self, changes: dict[str, dict[str, object]]
    ) -> None:
        """把少量状态回写到插件自己的配置文件。

        配置页没有按钮与只读文本控件，只能靠开关 + 保存触发动作、靠字段回写
        展示状态，因此这里集中处理三类回写：下载开关与浏览器就绪状态、备用
        端点迁移、以及供展示用的本插件当前地址。除此之外插件不写自己的配置。
        """
        raw = self._raw_config
        if not isinstance(raw, dict):
            return
        dirty = False
        for section, values in changes.items():
            target = raw.setdefault(section, {})
            if not isinstance(target, dict):
                continue
            for key, value in values.items():
                if target.get(key) != value:
                    target[key] = value
                    dirty = True
        if not dirty:
            return

        saver_async = getattr(raw, "save_config_async", None)
        saver_sync = getattr(raw, "save_config", None)
        try:
            if callable(saver_async):
                await saver_async()
            elif callable(saver_sync):
                saver_sync()
            else:  # 非 AstrBot 运行环境（如测试里的裸 dict）
                logger.debug("[t2i] 配置对象不支持回写，跳过")
                return
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"[t2i] 回写插件配置失败：{exc}")
            return
        logger.debug(f"[t2i] 插件配置已回写：{changes}")

    async def _prelaunch_browser(self) -> None:
        if self._closing:
            return
        if not playwright_pip_available() or not self.browser_manager.is_installed():
            return
        ok, detail = await self.server.render_probe(build_test_card(quiet=True))
        if ok:
            logger.info("[t2i] 预热浏览器完成")
        else:
            logger.warning(f"[t2i] 预热浏览器失败：{detail}")

    async def _apply_endpoint_config(self) -> None:
        """按配置把 AstrBot 主程序的 t2i 端点指向本插件（可选）。"""
        if not self.settings.endpoint.auto_configure:
            return
        try:
            endpoint = await self._write_endpoint_to_core_config()
            logger.info(
                f"[t2i] 已把 AstrBot 文转图端点设置为 {endpoint}，并打开文本转图像输出"
            )
        except Exception as exc:
            logger.warning(
                f"[t2i] 写入 AstrBot 文转图端点失败（可在 WebUI 手动设置）：{exc}"
            )

    async def _write_endpoint_to_core_config(self) -> str:
        """把端点写进 AstrBot 主配置并打开总开关，返回写入的地址。"""
        endpoint = self.settings.resolve_service_url()
        core_config = self.context.get_config()
        core_config["t2i_endpoint"] = endpoint
        core_config["t2i_strategy"] = "remote"
        # AstrBot 的「文本转图像输出」总开关关着时，t2i_endpoint 在它的配置页是隐藏项、
        # 也不会被读取，只写地址等于没写；开这开关就是这个插件的用途，一并打开
        core_config["t2i"] = True
        await _save_config(core_config)
        return endpoint

    async def _periodic_maintenance(self) -> None:
        """周期性清理过期图片 + 释放闲置 Chromium。"""
        while not self._closing:
            await asyncio.sleep(CLEANUP_TICK_SECONDS)
            if self._closing:
                break
            try:
                lifetime = self.settings.storage.image_lifetime_hours * 3600
                await asyncio.to_thread(
                    cleanup_expired_files, str(self.images_dir), lifetime
                )
            except Exception as exc:  # pragma: no cover - 防御
                logger.debug(f"[t2i] 清理过期图片失败：{exc}")
            try:
                await self.renderer.close_idle(
                    self.settings.render.idle_timeout_seconds
                )
            except Exception as exc:  # pragma: no cover - 防御
                logger.debug(f"[t2i] 释放闲置 Chromium 失败：{exc}")

    # -------------------------------------------------------------- 状态信息

    def _status_payload(self) -> dict:
        return {
            "plugin_name": PLUGIN_NAME,
            "plugin_version": _plugin_version(),
            "plugin_uptime": round(time.time() - self.loaded_at, 1),
            "server": {
                "enabled": self.settings.server.enabled,
                "running": self.server.running,
                "endpoint": self.server.endpoint,
                "requests": self.server.requests_served,
                "fallback_used": self.server.fallback_used,
                "last_error": self.server.last_error,
            },
            "browser": self.browser_manager.status(),
            "renderer": {
                "connected": self.renderer.is_browser_connected(),
                "concurrency": self.settings.render.concurrency,
                "idle_timeout": self.settings.render.idle_timeout_seconds,
            },
            "endpoint": {
                "auto_configure": self.settings.endpoint.auto_configure,
                "target": self.settings.resolve_service_url(),
                "astrbot_t2i_endpoint": self._read_core_config("t2i_endpoint"),
                "astrbot_t2i_strategy": self._read_core_config("t2i_strategy"),
                "astrbot_t2i_enabled": self._read_core_flag("t2i"),
            },
        }

    def _read_core_config(self, key: str) -> str:
        try:
            return str(self.context.get_config().get(key, ""))
        except Exception:  # pragma: no cover - 防御
            return ""

    def _read_core_flag(self, key: str) -> bool:
        try:
            return bool(self.context.get_config().get(key, False))
        except Exception:  # pragma: no cover - 防御
            return False

    def _status_text(self) -> str:
        payload = self._status_payload()
        server = payload["server"]
        browser = payload["browser"]
        renderer = payload["renderer"]
        endpoint = payload["endpoint"]

        if browser["installed"]:
            chromium = f"已安装（{Path(browser['chromium_path']).name}）"
        else:
            chromium = "未安装"
        if not browser["installed"]:
            deps = "装好 Chromium 后检查"
        elif browser["missing_libs"]:
            deps = f"缺少 {'、'.join(browser['missing_libs'])}"
        else:
            deps = "齐全"
        lines = [
            "【文转图服务状态】",
            f"内嵌服务：{'运行中' if server['running'] else '未运行'}"
            f"（{server['endpoint']}）",
            f"累计请求：{server['requests']} 次；透传备用端点 {server['fallback_used']} 次",
            f"Python playwright：{'已安装' if browser['pip_playwright'] else '未安装'}",
            f"Chromium：{chromium}",
            f"系统依赖：{deps}",
            f"浏览器进程：{browser['processes']} 个，占用 {browser['process_memory_mb']} MB",
            f"浏览器连接：{'已连接' if renderer['connected'] else '空闲/未启动'}",
            f"插件浏览器目录：{browser['browsers_path']}",
            f"安装任务：{browser['install']['status']} - {browser['install']['message']}",
            f"AstrBot 文转图端点：{endpoint['astrbot_t2i_endpoint'] or '（未设置）'}"
            f"（策略：{endpoint['astrbot_t2i_strategy'] or '未知'}，"
            f"输出开关：{'开' if endpoint['astrbot_t2i_enabled'] else '关'}）",
        ]
        if browser["deps"]["status"] != "idle":
            lines.append(
                f"依赖补装：{browser['deps']['status']} - {browser['deps']['message']}"
            )
        if server["last_error"]:
            lines.append(f"最近错误：{server['last_error']}")
        if not browser["pip_playwright"]:
            lines.append("提示：重装插件可让 AstrBot 自动安装 playwright 依赖")
        elif not browser["installed"]:
            lines.append(
                "提示：发送 /t2i_install 安装 Chromium，"
                "或在插件配置里打开「下载 Chromium 浏览器」并保存"
            )
        elif browser["missing_libs"]:
            lines.append("提示：Chromium 缺少系统运行库，发送 /t2i_install 可自动补装")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 命令

    @filter.command("t2i_status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看文转图服务与 Chromium 状态。"""
        yield event.plain_result(self._status_text())

    def _install_plan(self) -> str:
        """决定 /t2i_install 该做什么：装浏览器、只补系统库，还是什么都不用做。"""
        manager = self.browser_manager
        if not manager.is_installed():
            return "install"
        return "deps" if manager.missing_shared_libs() else "none"

    def _install_progress_text(self, state_name: str) -> str:
        """任务已在跑时回报当前进度，而不是只丢一句「已有任务在进行中」。"""
        state = getattr(self.browser_manager, state_name)
        label = "补装系统依赖" if state_name == "deps_state" else "安装 Chromium"
        return (
            f"已有任务在进行：正在{label}，已用时 {state.elapsed:.0f}s｜{state.message}\n"
            "继续为你推送进度…"
        )

    def _install_result_text(self, state: InstallState, state_name: str) -> str:
        if state.status != DONE:
            return f"❌ 安装未成功：{state.message}"
        if state_name == "deps_state":
            return f"✅ {state.message}\n可以发送 /t2i_test 验证渲染效果。"
        return (
            f"✅ {state.message}\n浏览器路径：{self.browser_manager.find_chromium()}\n"
            "可以发送 /t2i_test 验证渲染效果。"
        )

    def _install_hint(self) -> str:
        """渲染失败时补一句「现在能做什么」：正在补装就给进度，否则给下一步动作。"""
        manager = self.browser_manager
        if manager.deps_state.running:
            return (
                f"\n\n系统依赖正在补装（已用时 {manager.deps_state.elapsed:.0f}s）："
                f"{manager.deps_state.message}\n补装完成后再试一次即可。"
            )
        if manager.install_state.running:
            return f"\n\nChromium 正在安装（已用时 {manager.install_state.elapsed:.0f}s），完成后重试。"
        if manager.is_installed() and manager.missing_shared_libs():
            if deps_install_available():
                return "\n\n可发送 /t2i_install 自动补装系统依赖。"
            return (
                "\n\n补装需要 AstrBot 进程为 root 且容器内有 apt-get/dnf；"
                "也可手动执行 `playwright install-deps chromium`。"
            )
        return ""

    async def _follow_install(
        self, event: AstrMessageEvent, state_name: str
    ) -> AsyncIterator[MessageEventResult]:
        """轮询安装状态并推送进度；日志尾巴里就是用户最关心的下载/安装进度。"""
        manager = self.browser_manager
        limit = self.settings.browser.install_timeout_seconds
        started = time.time()
        sent = 0
        while True:
            state = getattr(manager, state_name)
            if not state.running:
                break
            await asyncio.sleep(INSTALL_POLL_SECONDS)
            state = getattr(manager, state_name)
            if not state.running:
                break
            waited = time.time() - started
            if waited > limit:
                break
            if sent >= INSTALL_PROGRESS_MAX:
                continue
            if waited < (sent + 1) * INSTALL_PROGRESS_INTERVAL:
                continue
            sent += 1
            tail = "\n".join(state.log_tail[-2:])
            yield event.plain_result(
                f"安装中…已用时 {waited:.0f}s｜{state.message}"
                + (f"\n{tail}" if tail else "")
            )

    @filter.command("t2i_install")
    async def cmd_install(self, event: AstrMessageEvent):
        """安装 Chromium；浏览器已就绪但缺系统库时改为补装系统依赖。"""
        if not playwright_pip_available():
            yield event.plain_result(
                "未检测到 Python 包 playwright。请先在 AstrBot 中重装本插件"
                "（会自动安装 requirements.txt），或在 AstrBot 环境执行 "
                "`pip install playwright`。"
            )
            return
        manager = self.browser_manager
        if manager.install_state.running or manager.deps_state.running:
            running_state = "deps_state" if manager.deps_state.running else "install_state"
            yield event.plain_result(self._install_progress_text(running_state))
            async for progress in self._follow_install(event, running_state):
                yield progress
            yield event.plain_result(
                self._install_result_text(
                    getattr(manager, running_state), running_state
                )
            )
            return

        plan = self._install_plan()
        if plan == "none":
            yield event.plain_result(
                f"Chromium 已安装且系统依赖齐全：{manager.find_chromium()}\n"
                "如需强制重装，请删除插件数据目录下的 browsers 文件夹后重试。"
            )
            return

        if plan == "deps":
            if not deps_install_available():
                yield event.plain_result(
                    "❌ Chromium 缺少系统运行库："
                    f"{'、'.join(manager.missing_shared_libs())}\n"
                    "自动补装需要 AstrBot 进程为 root 且容器内有 apt-get/dnf；"
                    "请手动执行 `playwright install-deps chromium`。"
                )
                return
            manager.start_install_deps()
            yield event.plain_result(
                f"Chromium 已安装，但缺少系统运行库："
                f"{'、'.join(manager.missing_shared_libs())}\n正在自动补装…"
            )
            state_name = "deps_state"
        else:
            manager.start_install(install_deps=self.settings.browser.auto_install_deps)
            yield event.plain_result(
                "已开始安装 Chromium…\n"
                f"下载源：{manager.download_host_effective() or '默认 CDN'}\n"
                f"安装目录：{manager.browsers_path}"
            )
            state_name = "install_state"

        async for progress in self._follow_install(event, state_name):
            yield progress

        state = getattr(manager, state_name)
        if state.status == DONE and state_name == "install_state":
            await self._apply_endpoint_config()
        yield event.plain_result(self._install_result_text(state, state_name))

    @filter.command("t2i_test")
    async def cmd_test(self, event: AstrMessageEvent):
        """渲染一张测试卡片，验证文转图链路是否正常。"""
        payload = self._status_payload()
        html = build_test_card(
            quiet=False,
            server_running=payload["server"]["running"],
            endpoint=payload["server"]["endpoint"],
            chromium_installed=payload["browser"]["installed"],
            chromium_path=payload["browser"]["chromium_path"],
            renderer_connected=payload["renderer"]["connected"],
        )
        started = time.perf_counter()
        ok, detail = await self.server.render_probe(html)
        elapsed = time.perf_counter() - started
        if ok:
            yield event.image_result(detail)
            yield event.plain_result(f"渲染成功，用时 {elapsed * 1000:.0f} ms")
        else:
            yield event.plain_result(f"渲染失败：{detail}{self._install_hint()}")

    @filter.command("t2i_restart")
    async def cmd_restart(self, event: AstrMessageEvent):
        """重启内嵌服务并释放 Chromium。"""
        try:
            await self.renderer.terminate()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 关闭 Chromium 失败：{exc}")
        if not self.settings.server.enabled:
            yield event.plain_result(
                "内嵌服务未启用（server.enabled = false），已释放 Chromium。"
            )
            return
        ok, message = await self.server.restart()
        if ok:
            yield event.plain_result(f"✅ 内嵌服务已重启：{self.server.endpoint}")
        else:
            yield event.plain_result(f"❌ 重启失败：{message}")

    # ------------------------------------------------------------- Web API

    def _register_web_apis(self) -> None:
        if self._web_apis_registered:
            return
        base = f"/{PLUGIN_NAME}"
        self.context.register_web_api(
            f"{base}/status", self.web_status, ["GET"], "文转图服务状态"
        )
        self.context.register_web_api(
            f"{base}/install", self.web_install, ["POST"], "安装 Chromium"
        )
        self.context.register_web_api(
            f"{base}/install/state",
            self.web_install_state,
            ["GET"],
            "Chromium 安装进度",
        )
        self.context.register_web_api(
            f"{base}/render-test", self.web_render_test, ["POST"], "渲染测试卡片"
        )
        self.context.register_web_api(
            f"{base}/apply-endpoint",
            self.web_apply_endpoint,
            ["POST"],
            "让 AstrBot 主程序使用本插件端点",
        )
        self.context.register_web_api(
            f"{base}/restart", self.web_restart, ["POST"], "重启内嵌服务"
        )
        self.context.register_web_api(
            f"{base}/status-card", self.web_status_card, ["GET"], "状态卡片图片"
        )
        self._web_apis_registered = True

    async def web_status(self):
        return json_response({"status": "ok", "data": self._status_payload()})

    async def web_install(self):
        if not playwright_pip_available():
            return error_response(
                "未安装 Python 包 playwright，请重装插件以自动安装依赖", status_code=400
            )
        manager = self.browser_manager
        chromium = manager.find_chromium()
        if chromium is not None:
            missing = manager.missing_shared_libs()
            if not missing:
                return json_response(
                    {
                        "status": "ok",
                        "data": {
                            "already_installed": True,
                            "chromium_path": str(chromium),
                        },
                    }
                )
            if not deps_install_available():
                return error_response(
                    "Chromium 缺少系统运行库（"
                    + "、".join(missing)
                    + "）；自动补装需要 root 与 apt-get/dnf，"
                    "请手动执行 `playwright install-deps chromium`",
                    status_code=400,
                )
            return json_response(
                {
                    "status": "ok",
                    "data": {
                        "started": manager.start_install_deps(),
                        "mode": "deps",
                        "missing_libs": missing,
                    },
                }
            )
        started = manager.start_install(
            install_deps=self.settings.browser.auto_install_deps
        )
        return json_response(
            {"status": "ok", "data": {"started": started, "mode": "install"}}
        )

    async def web_install_state(self):
        manager = self.browser_manager
        return json_response(
            {
                "status": "ok",
                "data": {
                    **manager.install_state.as_dict(),
                    "installed": manager.is_installed(),
                    "missing_libs": manager.missing_shared_libs(),
                    "deps": manager.deps_state.as_dict(),
                },
            }
        )

    async def web_render_test(self):
        payload = self._status_payload()
        html = build_test_card(
            quiet=False,
            server_running=payload["server"]["running"],
            endpoint=payload["server"]["endpoint"],
            chromium_installed=payload["browser"]["installed"],
            chromium_path=payload["browser"]["chromium_path"],
            renderer_connected=payload["renderer"]["connected"],
        )
        started = time.perf_counter()
        ok, detail = await self.server.render_probe(html)
        elapsed = time.perf_counter() - started
        if not ok:
            return json_response(
                {
                    "status": "error",
                    "message": f"{detail}{self._install_hint()}",
                    "data": {"ok": False},
                }
            )
        image = _read_as_data_url(detail)
        if image is None:
            return error_response("读取渲染结果失败", status_code=500)
        return json_response(
            {
                "status": "ok",
                "data": {
                    "ok": True,
                    "elapsed_ms": round(elapsed * 1000, 1),
                    "image": image,
                },
            }
        )

    async def web_apply_endpoint(self):
        try:
            endpoint = await self._write_endpoint_to_core_config()
        except Exception as exc:
            return error_response(f"写入失败：{exc}", status_code=500)
        return json_response(
            {
                "status": "ok",
                "data": {
                    "endpoint": endpoint,
                    "strategy": "remote",
                    "enabled": True,
                    "note": "若 AstrBot 已缓存文转图策略，重启 AstrBot 后生效",
                },
            }
        )

    async def web_restart(self):
        try:
            await self.renderer.terminate()
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[t2i] 关闭 Chromium 失败：{exc}")
        if not self.settings.server.enabled:
            return json_response(
                {"status": "ok", "data": {"enabled": False, "running": False}}
            )
        ok, message = await self.server.restart()
        return json_response(
            {
                "status": "ok" if ok else "error",
                "message": message,
                "data": {
                    "running": self.server.running,
                    "endpoint": self.server.endpoint,
                },
            }
        )

    async def web_status_card(self):
        ok, detail = await self.server.render_probe(
            build_status_card(self._status_payload())
        )
        if not ok:
            return error_response(detail, status_code=503)
        image = _read_as_data_url(detail)
        if image is None:
            return error_response("读取渲染结果失败", status_code=500)
        return json_response({"status": "ok", "data": {"image": image}})



def _plugin_version() -> str:
    """从 metadata.yaml 读插件版本（唯一版本源，见 AGENTS.md 7.1）。

    不在代码里手写版本常量：手写值会与 metadata.yaml 漂移，
    状态卡片和 WebUI 页面就会显示错误版本号。
    """
    try:
        import yaml

        data = yaml.safe_load(
            (_PLUGIN_DIR / "metadata.yaml").read_text(encoding="utf-8")
        )
    except (OSError, ImportError, ValueError):
        return ""
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else ""


async def _save_config(core_config) -> None:
    """持久化 AstrBot 配置：新版有 async 版本，旧版回退到同步写入。"""
    save_async = getattr(core_config, "save_config_async", None)
    if save_async is not None:
        await save_async()
    else:  # pragma: no cover - 旧版 AstrBot 兼容
        core_config.save_config()


def _read_as_data_url(path: str) -> str | None:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    suffix = Path(path).suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(raw).decode()
