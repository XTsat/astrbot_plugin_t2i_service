from __future__ import annotations

import asyncio
import os
import re
import time

from jinja2.sandbox import SandboxedEnvironment
from .logger import get_logger as _get_logger

logger = _get_logger()
from pydantic import BaseModel
from typing import Literal
from typing_extensions import TypedDict

try:  # playwright 未安装时仍然允许插件加载并提示用户安装
    from playwright._impl._errors import TargetClosedError
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Playwright,
        async_playwright,
    )

    PLAYWRIGHT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - 取决于运行环境
    Browser = BrowserContext = Playwright = object  # type: ignore[misc,assignment]
    async_playwright = None  # type: ignore[assignment]

    class TargetClosedError(Exception):  # type: ignore[no-redef]
        """playwright 缺失时的占位异常。"""

    PLAYWRIGHT_AVAILABLE = False


class PlaywrightNotInstalledError(RuntimeError):
    """Python 包 playwright 或 Chromium 浏览器不可用。"""


from .metrics import (
    BROWSER_CONNECTED,
    BROWSER_CONTEXTS,
    BROWSER_RESTARTS,
    BROWSER_STARTS,
    RENDER_ACTIVE_PAGES,
    RENDER_DURATION,
    RENDER_IN_PROGRESS,
    RENDER_OUTPUT_BYTES,
    RENDER_REQUESTS,
    RENDER_VIEWPORT_PIXELS,
)
from .util import generate_data_path


class FloatRect(TypedDict):
    x: float
    y: float
    width: float
    height: float


class ScreenshotOptions(BaseModel):
    """Playwright 截图参数

    详见：https://playwright.dev/python/docs/api/class-page#page-screenshot

    Args:
        timeout (float, optional): 截图超时时间.
        type (Literal["jpeg", "png"], optional): 截图图片类型.
        path (Union[str, Path]], optional): 截图保存路径，如不需要则留空.
        quality (int, optional): 截图质量，仅适用于 JPEG 格式图片.
        omit_background (bool, optional): 是否允许隐藏默认的白色背景，这样就可以截透明图了，仅适用于 PNG 格式.
        full_page (bool, optional): 是否截整个页面而不是仅设置的视口大小，默认为 True.
        clip (FloatRect, optional): 截图后裁切的区域，xy为起点.
        animations: (Literal["allow", "disabled"], optional): 是否允许播放 CSS 动画.
        caret: (Literal["hide", "initial"], optional): 当设置为 `hide` 时，截图时将隐藏文本插入符号，默认为 `hide`.
        scale: (Literal["css", "device"], optional): 页面缩放设置.
            当设置为 `css` 时，则将设备分辨率与 CSS 中的像素一一对应，在高分屏上会使得截图变小.
            当设置为 `device` 时，则根据设备的屏幕缩放设置或当前 Playwright 的 Page/Context 中的
            device_scale_factor 参数来缩放.
        viewport_width: (int, optional): 自定义视口宽度，用于控制截图宽度.
            优先级：
            1. 显式指定此参数；
            2. 从 HTML 的 <meta name="viewport" content="width=..."> 自动解析；
            3. 未指定时默认为 800px.
        viewport_height: (int, optional): 自定义视口高度，用于控制截图高度.
            优先级：
            1. 显式指定此参数；
            2. 从 HTML 的 <meta name="viewport" content="height=..."> 自动解析；
            3. 未指定时默认为 720px.
        device_scale_factor_level: (Literal["normal", "high", "ultra"], optional): 设备像素比等级.
            - normal: 1.0
            - high: 1.3
            - ultra: 1.8

    @author: Redlnn(https://github.com/GraiaCommunity/graiax-text2img-playwright)
    """

    timeout: float | None = None
    type: Literal["jpeg", "png", None] = None
    quality: int | None = None
    omit_background: bool | None = None
    full_page: bool | None = True
    clip: FloatRect | None = None
    animations: Literal["allow", "disabled", None] = None
    caret: Literal["hide", "initial", None] = None
    scale: Literal["css", "device", None] = None
    viewport_width: int | None = None
    viewport_height: int | None = None
    device_scale_factor_level: Literal["normal", "high", "ultra", None] = None


class Text2ImgRender:
    # Mapping from device_scale_factor_level to actual device_scale_factor
    SCALE_FACTOR_MAP = {
        "normal": 1.0,
        "high": 1.3,
        "ultra": 1.8,
    }

    def __init__(
        self,
        data_dir: str = "data",
        browsers_path: str | None = None,
        no_sandbox: bool = False,
        concurrency: int = 2,
        executable_path: str | None = None,
        launch_args: list[str] | None = None,
    ):
        """
        Args:
            data_dir: 渲染中间产物（html/图片）存放目录。
            browsers_path: Chromium 安装目录，会写入 ``PLAYWRIGHT_BROWSERS_PATH``。
                必须在 Playwright 首次启动前设置。
            no_sandbox: 以 ``--no-sandbox`` 启动 Chromium（容器内 root 用户必需）。
            concurrency: 同时进行的截图数量上限。
            executable_path: 自定义 Chromium 可执行文件路径。
            launch_args: 追加的 Chromium 启动参数。
        """
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        # Context pool: {"normal": context, "high": context, "ultra": context}
        self.contexts: dict[str, BrowserContext] = {}
        self.data_dir = data_dir
        self.browsers_path = browsers_path
        self.no_sandbox = no_sandbox
        self.executable_path = executable_path
        self.launch_args = list(launch_args or [])
        self._last_used = time.monotonic()
        self._sem = asyncio.Semaphore(max(1, int(concurrency)))

        if browsers_path:
            # Playwright 在启动 driver 时读取该环境变量，必须早于 launch 设置。
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path

    @property
    def last_used(self) -> float:
        """最近一次使用渲染器的时间（单调时钟）。"""
        return self._last_used

    def is_browser_connected(self) -> bool:
        """Chromium 是否处于已连接状态。"""
        return self.browser is not None and bool(self.browser.is_connected())

    def _launch_options(self) -> dict:
        args = list(self.launch_args)
        if self.no_sandbox and "--no-sandbox" not in args:
            args.append("--no-sandbox")
        options: dict = {"headless": True}
        if args:
            options["args"] = args
        if self.executable_path:
            options["executable_path"] = self.executable_path
        return options

    async def _ensure_context(self, level: str = "normal") -> BrowserContext:
        """Ensure that Playwright, Browser and BrowserContext are initialized.

        Args:
            level: Device scale factor level ("normal", "high", or "ultra").
                   Defaults to "normal" if not specified.

        Returns:
            The BrowserContext for the specified level.

        Raises:
            PlaywrightNotInstalledError: playwright 未安装。
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise PlaywrightNotInstalledError(
                "未安装 Python 包 playwright，请在 AstrBot 环境执行 "
                "`pip install playwright`（或重装本插件以自动安装依赖）。"
            )

        if self.playwright is None:
            self.playwright = await async_playwright().start()

        # ensure browser launched
        if self.browser is None or not self.browser.is_connected():
            if self.browser is not None:
                try:
                    await self.browser.close()
                except Exception as e:
                    logger.debug(f"Close old browser failed: {e}")
            self.browser = await self.playwright.chromium.launch(
                **self._launch_options()
            )
            BROWSER_STARTS.inc()
            BROWSER_CONNECTED.set(1)
            logger.info(
                f"Chromium launched (no_sandbox={self.no_sandbox}, "
                f"executable={self.executable_path or 'playwright-bundled'})"
            )

        # ensure context available for the specified level
        if level not in self.contexts:
            scale_factor = self.SCALE_FACTOR_MAP.get(level, 1.0)
            self.contexts[level] = await self.browser.new_context(
                device_scale_factor=scale_factor,
            )
            logger.info(
                f"Created context for level '{level}' with device_scale_factor={scale_factor}"
            )
            BROWSER_CONTEXTS.set(len(self.contexts))

        return self.contexts[level]

    async def from_jinja_template(self, template: str, data: dict) -> tuple[str, str]:
        env = SandboxedEnvironment()
        html = env.from_string(template).render(data)
        return await self.from_html(html)

    async def from_html(self, html: str) -> tuple[str, str]:
        html_file_path, abs_path = generate_data_path(
            suffix="html", namespace="rendered", data_dir=self.data_dir
        )
        with open(html_file_path, "w", encoding="utf-8") as f:
            f.write(html)
        return html_file_path, abs_path

    def _resolve_viewport_size(
        self, html_file_path: str, screenshot_options: ScreenshotOptions
    ) -> tuple[int | None, int | None]:
        """根据截图参数与 HTML 内容推断 viewport 大小（宽, 高）。

        优先级：
        1. 调用方在 ScreenshotOptions 中显式指定 `viewport_width` / `viewport_height`；
        2. 从 HTML 中的 `<meta name="viewport" content="width=...; height=...">` 自动解析；
        3. 未能解析到时返回对应的 None（调用方可选择使用 Playwright 默认值）。

        将逻辑集中到独立方法，便于后续扩展：
        - 支持更多 meta 语法 / 自定义 data-* 属性；
        - 支持从额外配置源中读取默认宽度等。
        """

        viewport_width: int | None = screenshot_options.viewport_width
        viewport_height: int | None = screenshot_options.viewport_height

        # 如果两者都有显式值，直接返回
        if viewport_width is not None and viewport_height is not None:
            return viewport_width, viewport_height

        # 未指定时，尝试从 HTML meta 中解析（只读前几 KB 即可命中 <head> 区域）
        try:
            with open(html_file_path, "r", encoding="utf-8") as f:
                head_snippet = f.read(4096)

            # 尝试解析宽度和高度（允许任意顺序出现在 content 中）
            if viewport_width is None:
                pattern = (
                    r'<meta\s+[^>]*name=["\']viewport["\'][^>]*'
                    r'content=["\'][^"\']*width\s*=\s*(\d+)[^"\']*["\'][^>]*>'
                )
                if m := re.search(pattern, head_snippet, re.IGNORECASE):
                    viewport_width = int(m[1])

            if viewport_height is None:
                pattern = (
                    r'<meta\s+[^>]*name=["\']viewport["\'][^>]*'
                    r'content=["\'][^"\']*height\s*=\s*(\d+)[^"\']*["\'][^>]*>'
                )
                if m := re.search(pattern, head_snippet, re.IGNORECASE):
                    viewport_height = int(m[1])
        except (OSError, UnicodeDecodeError, re.error, ValueError) as e:
            logger.debug(f"Adjust viewport from meta tag failed: {e}")

        return viewport_width, viewport_height

    async def terminate(self) -> None:
        """Terminate Playwright and close browser."""
        # Close all contexts in the pool
        for level, context in list(self.contexts.items()):
            try:
                await context.close()
                logger.debug(f"Closed context for level '{level}'")
            except Exception as e:
                logger.debug(f"Close context for level '{level}' failed: {e}")
        self.contexts.clear()
        BROWSER_CONTEXTS.set(0)

        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception as e:
                logger.debug(f"Close browser failed: {e}")
            self.browser = None
        BROWSER_CONNECTED.set(0)

        if self.playwright is not None:
            try:
                await self.playwright.stop()
            except Exception as e:
                logger.debug(f"Stop Playwright failed: {e}")
            self.playwright = None

    async def close_idle(self, idle_seconds: float) -> bool:
        """闲置超时后释放 Chromium，回收内存。

        Args:
            idle_seconds: 空闲秒数阈值；<=0 表示不回收。

        Returns:
            bool: 本次是否真的关闭了浏览器。
        """
        if idle_seconds <= 0:
            return False
        if self.browser is None and self.playwright is None:
            return False
        if time.monotonic() - self._last_used < idle_seconds:
            return False
        if self.browser is not None or self.playwright is not None:
            logger.info(f"Idle for {idle_seconds:.0f}s, releasing Chromium to free memory")
            await self.terminate()
            return True
        return False

    async def html2pic(
        self, html_file_path: str, screenshot_options: ScreenshotOptions
    ) -> str:
        """截图入口：并发限流 + 空闲时间戳更新。"""
        async with self._sem:
            self._last_used = time.monotonic()
            result = await self._screenshot(html_file_path, screenshot_options)
            self._last_used = time.monotonic()
            return result

    async def _screenshot(
        self, html_file_path: str, screenshot_options: ScreenshotOptions
    ) -> str:
        # Determine which context to use based on device_scale_factor_level
        level = screenshot_options.device_scale_factor_level or "normal"
        suffix = screenshot_options.type if screenshot_options.type else "png"
        started = time.perf_counter()
        result = "error"
        page = None
        RENDER_IN_PROGRESS.inc()
        try:
            context = await self._ensure_context(level)
            result_path, _ = generate_data_path(
                suffix=suffix, namespace="rendered", data_dir=self.data_dir
            )

            try:
                page = await context.new_page()
            except TargetClosedError as e:
                BROWSER_RESTARTS.labels(reason="target_closed").inc()
                logger.warning(
                    f"html2pic: Failed to create new page, restarting browser context: {e}"
                )
                # Close and remove the specific context, then recreate it
                if level in self.contexts:
                    try:
                        await self.contexts[level].close()
                    except Exception:
                        pass
                    del self.contexts[level]
                    BROWSER_CONTEXTS.set(len(self.contexts))
                context = await self._ensure_context(level)
                page = await context.new_page()
            RENDER_ACTIVE_PAGES.inc()

            viewport_width, viewport_height = self._resolve_viewport_size(
                html_file_path, screenshot_options
            )

            width = viewport_width if viewport_width is not None else 800
            height = viewport_height if viewport_height is not None else 720
            scale_factor = self.SCALE_FACTOR_MAP.get(level, 1.0)
            RENDER_VIEWPORT_PIXELS.labels(scale=level).observe(
                width * height * scale_factor * scale_factor
            )
            # Always set viewport size to ensure defaults are applied
            await page.set_viewport_size({"width": width, "height": height})
            logger.info(f"html2pic: set viewport size to {width}x{height}")

            try:
                await page.goto(
                    f"file://{html_file_path}", timeout=screenshot_options.timeout
                )
                screenshot_kwargs = screenshot_options.model_dump(exclude_none=True)
                screenshot_kwargs.pop("viewport_width", None)
                screenshot_kwargs.pop("viewport_height", None)
                screenshot_kwargs.pop("device_scale_factor_level", None)

                # Robustness: Remove quality if type is png, as Playwright errors out
                if screenshot_options.type == "png":
                    screenshot_kwargs.pop("quality", None)

                await page.screenshot(path=result_path, **screenshot_kwargs)
            finally:
                # Ensure the page is closed to free resources
                await page.close()
                page = None
                RENDER_ACTIVE_PAGES.dec()

            RENDER_OUTPUT_BYTES.labels(format=suffix).observe(
                os.path.getsize(result_path)
            )
            result = "success"
            logger.info(f"Rendered {html_file_path} to {result_path}")
            return result_path
        finally:
            if page is not None:
                try:
                    await page.close()
                finally:
                    RENDER_ACTIVE_PAGES.dec()
            if self.browser is None or not self.browser.is_connected():
                BROWSER_CONNECTED.set(0)
            RENDER_REQUESTS.labels(
                result=result,
                format=suffix,
                scale=level,
            ).inc()
            RENDER_DURATION.labels(result=result, scale=level).observe(
                time.perf_counter() - started
            )
            RENDER_IN_PROGRESS.dec()
