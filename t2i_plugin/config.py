"""插件配置的读取、默认值与类型归一化。

AstrBot 的插件配置来自 `_conf_schema.json` 生成的那份 `config`，其中的值
可能来自 WebUI（类型正确）或旧版本配置文件（可能缺字段）。本模块把这份
原始 dict 归一化成一个只读的 `T2IConfig`，其余模块只依赖它，避免到处写
`config.get("x", default)`。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# AstrBot 官方文档给出两个公共端点，其中 soulter.top 目前回复 503 no available
# server，默认值取能用的那个；用户可改成自建服务地址。
DEFAULT_FALLBACK_ENDPOINT = "https://t2i.rcfortress.site/text2img"
DEFAULT_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/playwright"
DEFAULT_PORT = 8999

# AstrBot 自己的默认值（t2i_word_threshold=150，且运行时会把阈值夹到 ≥50），
# 插件这边保持一致，避免同一份设置在两处显示不同数字。
DEFAULT_WORD_THRESHOLD = 150
MIN_WORD_THRESHOLD = 50
COPY_EXTRA_MODES = ("merged", "single", "separate", "off")
DEFAULT_COPY_EXTRA = "merged"
# 状态文本与状态卡片共用同一份显示名，避免两处文案漂移
COPY_EXTRA_LABELS = {
    "merged": "合并转发",
    "single": "不合并（一条文本）",
    "separate": "拆分（每项一条）",
    "off": "关闭",
}
# 可以单独勾选的「值得复制」内容类型；勾选顺序即展示顺序
COPY_KINDS = ("links", "code", "config", "paths")
COPY_KIND_LABELS = {
    "links": "链接",
    "code": "代码块",
    "config": "配置行",
    "paths": "文件路径",
}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def format_copy_kinds_note(kinds: Sequence[str]) -> str:
    """把勾选的内容类型渲染成状态后缀；全选返回空串，全不选说明不会补发。"""
    picked = [kind for kind in COPY_KINDS if kind in set(kinds)]
    if len(picked) == len(COPY_KINDS):
        return ""
    if not picked:
        return "（未勾选任何内容，不会补发）"
    return "（仅 " + "、".join(COPY_KIND_LABELS[kind] for kind in picked) + "）"


def coerce_copy_kinds(value: Any) -> tuple[str, ...]:
    """归一化「识别哪些内容」：缺省=全选，未知项丢弃，保持固定顺序。"""
    if not isinstance(value, (list, tuple)):
        return COPY_KINDS
    picked = {str(item).strip() for item in value}
    return tuple(kind for kind in COPY_KINDS if kind in picked)


def coerce_word_threshold(value: Any) -> int:
    """归一化转图字数阈值：与 AstrBot 一致，最小 50，非法值回落默认值。"""
    try:
        return max(MIN_WORD_THRESHOLD, int(str(value).strip()))
    except (TypeError, ValueError):
        return DEFAULT_WORD_THRESHOLD


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


@dataclass
class ServerConfig:
    """内嵌 HTTP 服务配置（对外提供 /text2img 契约）。"""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    auth_token: str = ""
    fallback_enabled: bool = True
    fallback_endpoint: str = DEFAULT_FALLBACK_ENDPOINT

    @property
    def service_url(self) -> str:
        host = self.host or "127.0.0.1"
        if host in {"0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        return f"http://{host}:{self.port}/text2img"


@dataclass
class RenderConfig:
    """渲染行为配置。"""

    concurrency: int = 2
    no_sandbox: bool = True
    idle_timeout_seconds: int = 300
    prelaunch: bool = False
    default_type: str = "jpeg"
    default_quality: int = 40
    full_page: bool = True


@dataclass
class BrowserConfig:
    """Chromium 供给配置。"""

    browsers_path: str = ""
    download_host: str = DEFAULT_DOWNLOAD_HOST
    executable_path: str = ""
    auto_install_deps: bool = True
    cleanup_orphans: bool = True
    install_timeout_seconds: int = 900
    download_chromium: bool = False
    # 由插件按本地实际状态回写，配置页据此隐藏「下载 Chromium」开关
    chromium_ready: bool = False


@dataclass
class EndpointConfig:
    """把 AstrBot 主程序的文转图请求指向本插件。"""

    auto_configure: bool = False
    service_url: str = ""
    # 由插件按 host/port 回写，仅在配置页展示「该填进 AstrBot 的地址」，
    # 不参与 resolve_service_url 解析——手改的值会被下次启动覆盖
    current_service_url: str = ""


@dataclass
class OutputConfig:
    """AstrBot 文转图输出：什么时候把回复转成图片，图片里的可复制内容怎么补发。"""

    # 对应 AstrBot 主配置的 t2i / t2i_word_threshold，插件启动时写进去
    enabled: bool = False
    word_threshold: int = DEFAULT_WORD_THRESHOLD
    # 图片里值得复制的片段（链接、代码块、配置行、路径）的补发方式
    copy_extra: str = DEFAULT_COPY_EXTRA
    # 勾选哪些片段需要被识别出来补发；全不勾等于不补发
    copy_kinds: tuple[str, ...] = COPY_KINDS
    # 回写标记：首次启动先沿用 AstrBot 现有设置，避免升级时被插件默认值覆盖
    core_synced: bool = False


@dataclass
class FontsConfig:
    """字体供给配置（字体文件本身的下载/上传走插件页面的「文转图服务面板」）。"""

    enabled: bool = True
    github_proxy: str = ""
    upload_limit_mb: int = 128
    restart_browser_on_change: bool = True


@dataclass
class TemplatesConfig:
    """转图模板配置（只作用于随插件发布的模板）。"""

    # 模板里的天气 / 心情 / 台词按这个间隔换一批；太频繁会显得随机得没意义，太慢又像卡住
    refresh_minutes: int = 15


@dataclass
class StorageConfig:
    image_lifetime_hours: int = 24
    cleanup_interval_minutes: int = 60


@dataclass
class T2IConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    endpoint: EndpointConfig = field(default_factory=EndpointConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    fonts: FontsConfig = field(default_factory=FontsConfig)
    templates: TemplatesConfig = field(default_factory=TemplatesConfig)

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "T2IConfig":
        raw = raw if isinstance(raw, dict) else {}

        server_raw = _section(raw, "server")
        render_raw = _section(raw, "render")
        browser_raw = _section(raw, "browser")
        endpoint_raw = _section(raw, "endpoint")
        output_raw = _section(raw, "output")
        storage_raw = _section(raw, "storage")
        fonts_raw = _section(raw, "fonts")
        templates_raw = _section(raw, "templates")

        server = ServerConfig(
            enabled=_as_bool(server_raw.get("enabled"), True),
            host=_as_str(server_raw.get("host"), "127.0.0.1") or "127.0.0.1",
            port=_as_int(server_raw.get("port"), DEFAULT_PORT),
            auth_token=_as_str(server_raw.get("auth_token")),
            fallback_enabled=_as_bool(server_raw.get("fallback_enabled"), True),
            fallback_endpoint=_as_str(
                server_raw.get("fallback_endpoint"), DEFAULT_FALLBACK_ENDPOINT
            ),
        )

        render = RenderConfig(
            concurrency=max(1, _as_int(render_raw.get("concurrency"), 2)),
            no_sandbox=_as_bool(render_raw.get("no_sandbox"), True),
            idle_timeout_seconds=max(
                0, _as_int(render_raw.get("idle_timeout_seconds"), 300)
            ),
            prelaunch=_as_bool(render_raw.get("prelaunch"), False),
            default_type=_as_str(render_raw.get("default_type"), "jpeg") or "jpeg",
            default_quality=_as_int(render_raw.get("default_quality"), 40),
            full_page=_as_bool(render_raw.get("full_page"), True),
        )

        browser = BrowserConfig(
            browsers_path=_as_str(browser_raw.get("browsers_path")),
            download_host=_as_str(
                browser_raw.get("download_host"), DEFAULT_DOWNLOAD_HOST
            ),
            executable_path=_as_str(browser_raw.get("executable_path")),
            auto_install_deps=_as_bool(browser_raw.get("auto_install_deps"), True),
            cleanup_orphans=_as_bool(browser_raw.get("cleanup_orphans"), True),
            install_timeout_seconds=max(
                60, _as_int(browser_raw.get("install_timeout_seconds"), 900)
            ),
            download_chromium=_as_bool(browser_raw.get("download_chromium"), False),
            chromium_ready=_as_bool(browser_raw.get("chromium_ready"), False),
        )

        endpoint = EndpointConfig(
            auto_configure=_as_bool(endpoint_raw.get("auto_configure"), False),
            service_url=_as_str(endpoint_raw.get("service_url")),
            current_service_url=_as_str(endpoint_raw.get("current_service_url")),
        )

        copy_extra = _as_str(output_raw.get("copy_extra"), DEFAULT_COPY_EXTRA)
        if copy_extra not in COPY_EXTRA_MODES:
            copy_extra = DEFAULT_COPY_EXTRA
        output = OutputConfig(
            enabled=_as_bool(output_raw.get("enabled"), False),
            # 与 AstrBot 一致：阈值最小 50，非数字回落到默认值
            word_threshold=coerce_word_threshold(
                output_raw.get("word_threshold", DEFAULT_WORD_THRESHOLD)
            ),
            copy_extra=copy_extra,
            copy_kinds=coerce_copy_kinds(output_raw.get("copy_kinds")),
            core_synced=_as_bool(output_raw.get("core_synced"), False),
        )

        storage = StorageConfig(
            image_lifetime_hours=max(
                1, _as_int(storage_raw.get("image_lifetime_hours"), 24)
            ),
            cleanup_interval_minutes=max(
                5, _as_int(storage_raw.get("cleanup_interval_minutes"), 60)
            ),
        )

        fonts = FontsConfig(
            enabled=_as_bool(fonts_raw.get("enabled"), True),
            github_proxy=_as_str(fonts_raw.get("github_proxy")),
            upload_limit_mb=min(
                1024, max(1, _as_int(fonts_raw.get("upload_limit_mb"), 128))
            ),
            restart_browser_on_change=_as_bool(
                fonts_raw.get("restart_browser_on_change"), True
            ),
        )

        templates = TemplatesConfig(
            # 1 分钟是下限（再短就等于「每条消息都变」），上限一天：超过一天的间隔没意义
            refresh_minutes=min(
                1440, max(1, _as_int(templates_raw.get("refresh_minutes"), 15))
            ),
        )

        if server.port <= 0 or server.port > 65535:
            server.port = DEFAULT_PORT

        return cls(
            server=server,
            render=render,
            browser=browser,
            endpoint=endpoint,
            output=output,
            storage=storage,
            fonts=fonts,
            templates=templates,
        )

    def resolve_service_url(self) -> str:
        """返回要写入 AstrBot 主程序的 t2i 端点地址。"""
        if self.endpoint.service_url:
            return self.endpoint.service_url
        return self.server.service_url
