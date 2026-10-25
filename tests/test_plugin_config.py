"""T2IConfig 的归一化行为：WebUI 传字符串、字段缺失、非法端口都要稳妥。"""

from t2i_plugin.config import (
    DEFAULT_DOWNLOAD_HOST,
    DEFAULT_FALLBACK_ENDPOINT,
    DEFAULT_PORT,
    T2IConfig,
)


def test_defaults_match_upstream_port():
    config = T2IConfig.from_raw({})
    assert config.server.enabled is True
    assert config.server.port == DEFAULT_PORT == 8999
    assert config.server.fallback_endpoint == DEFAULT_FALLBACK_ENDPOINT
    assert config.browser.download_host == DEFAULT_DOWNLOAD_HOST
    assert config.endpoint.auto_configure is False
    # 默认启用 no_sandbox：AstrBot 官方镜像以 root 运行，Chromium 需要 --no-sandbox
    assert config.render.no_sandbox is True


def test_string_values_from_webui_are_coerced():
    config = T2IConfig.from_raw(
        {
            "server": {
                "enabled": "false",
                "port": "6200",
                "host": "  0.0.0.0  ",
                "auth_token": " tok ",
            },
            "render": {"concurrency": "0", "no_sandbox": "yes", "default_quality": "77"},
            "browser": {"cleanup_orphans": "off"},
            "storage": {"image_lifetime_hours": "1"},
        }
    )
    assert config.server.enabled is False
    assert config.server.port == 6200
    assert config.server.host == "0.0.0.0"
    assert config.server.auth_token == "tok"
    # 并发至少为 1，质量与保留时长原样透传
    assert config.render.concurrency == 1
    assert config.render.no_sandbox is True
    assert config.render.default_quality == 77
    assert config.browser.cleanup_orphans is False
    assert config.storage.image_lifetime_hours == 1


def test_invalid_values_fall_back_to_defaults():
    config = T2IConfig.from_raw(
        {
            "server": {"port": 70000},
            "render": {"concurrency": "abc"},
            "browser": {"install_timeout_seconds": 1},
            "storage": {"cleanup_interval_minutes": 0},
        }
    )
    assert config.server.port == DEFAULT_PORT
    assert config.render.concurrency == 2
    assert config.browser.install_timeout_seconds == 60
    assert config.storage.cleanup_interval_minutes == 5


def test_non_dict_sections_are_ignored():
    config = T2IConfig.from_raw({"server": "oops", "render": None})
    assert config.server.port == DEFAULT_PORT
    assert config.render.concurrency == 2


def test_resolve_service_url_prefers_explicit_override_and_normalizes_wildcard():
    config = T2IConfig.from_raw({})
    config.server.host = "0.0.0.0"
    assert config.resolve_service_url() == "http://127.0.0.1:8999/text2img"

    config.endpoint.service_url = "http://plugin.local:1234/text2img"
    assert config.resolve_service_url() == "http://plugin.local:1234/text2img"
