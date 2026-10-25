"""插件配置的读取、默认值与类型归一化。

AstrBot 的插件配置来自 `_conf_schema.json` 生成的那份 `config`，其中的值
可能来自 WebUI（类型正确）或旧版本配置文件（可能缺字段）。本模块把这份
原始 dict 归一化成一个只读的 `T2IConfig`，其余模块只依赖它，避免到处写
`config.get("x", default)`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_FALLBACK_ENDPOINT = "https://t2i.soulter.top/text2img"
DEFAULT_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/playwright"
DEFAULT_PORT = 8999


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
    auto_install_deps: bool = False
    cleanup_orphans: bool = True
    install_timeout_seconds: int = 900


@dataclass
class EndpointConfig:
    """把 AstrBot 主程序的文转图请求指向本插件。"""

    auto_configure: bool = False
    service_url: str = ""


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
    storage: StorageConfig = field(default_factory=StorageConfig)

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "T2IConfig":
        raw = raw if isinstance(raw, dict) else {}

        server_raw = _section(raw, "server")
        render_raw = _section(raw, "render")
        browser_raw = _section(raw, "browser")
        endpoint_raw = _section(raw, "endpoint")
        storage_raw = _section(raw, "storage")

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
            auto_install_deps=_as_bool(browser_raw.get("auto_install_deps"), False),
            cleanup_orphans=_as_bool(browser_raw.get("cleanup_orphans"), True),
            install_timeout_seconds=max(
                60, _as_int(browser_raw.get("install_timeout_seconds"), 900)
            ),
        )

        endpoint = EndpointConfig(
            auto_configure=_as_bool(endpoint_raw.get("auto_configure"), False),
            service_url=_as_str(endpoint_raw.get("service_url")),
        )

        storage = StorageConfig(
            image_lifetime_hours=max(
                1, _as_int(storage_raw.get("image_lifetime_hours"), 24)
            ),
            cleanup_interval_minutes=max(
                5, _as_int(storage_raw.get("cleanup_interval_minutes"), 60)
            ),
        )

        if server.port <= 0 or server.port > 65535:
            server.port = DEFAULT_PORT

        return cls(
            server=server,
            render=render,
            browser=browser,
            endpoint=endpoint,
            storage=storage,
        )

    def resolve_service_url(self) -> str:
        """返回要写入 AstrBot 主程序的 t2i 端点地址。"""
        if self.endpoint.service_url:
            return self.endpoint.service_url
        return self.server.service_url
