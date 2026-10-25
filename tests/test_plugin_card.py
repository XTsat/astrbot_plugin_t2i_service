"""卡片 HTML 的构造规则：自包含（断网可渲染）、转义、内容正确。"""

from __future__ import annotations

import pytest

from t2i_plugin.card import build_status_card, build_test_card

# 断网可渲染 = 不加载任何外部资源（纯文本里出现地址没关系）
EXTERNAL_MARKERS = ("<link", "<script", "@import", "src=", "url(http", 'href="http')


def test_test_card_is_self_contained():
    html = build_test_card(
        server_running=True,
        endpoint="http://127.0.0.1:6231/text2img",
        chromium_installed=True,
        chromium_path="/data/browsers/chromium-1248/chrome-linux64/chrome",
        renderer_connected=True,
    )
    assert html.startswith("<!DOCTYPE html>")
    assert 'charset="utf-8"' in html.lower() or "charset=utf-8" in html.lower()
    assert "文转图服务自检" in html
    assert "RUNNING" in html
    assert "READY" in html
    assert "chrome" in html
    # 断网环境也能出图：不允许任何外链资源
    lowered = html.lower()
    for marker in EXTERNAL_MARKERS:
        assert marker not in lowered, marker


def test_test_card_escapes_untrusted_endpoint():
    html = build_test_card(
        server_running=True,
        endpoint='http://x"><script>alert(1)</script>',
        chromium_installed=False,
    )
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_quiet_test_card_is_lightweight():
    quiet = build_test_card(quiet=True)
    full = build_test_card()
    assert len(quiet) < len(full)
    assert "预热" in quiet


def test_status_card_renders_payload_values():
    payload = {
        "server": {
            "running": True,
            "endpoint": "http://127.0.0.1:6231/text2img",
            "requests": 12,
            "fallback_used": 3,
        },
        "browser": {
            "installed": True,
            "pip_playwright": True,
            "processes": 2,
            "process_memory_mb": 88.5,
            "browsers_path": "/data/browsers",
            "chromium_path": "/data/browsers/chromium-1248/chrome-linux64/chrome",
            "download_host": "",
            "install": {"status": "done", "message": "已安装", "elapsed": 12.5},
        },
        "renderer": {"connected": True, "concurrency": 4, "pending": 1},
        "endpoint": {
            "auto_configure": True,
            "native_t2i_network": True,
            "plugin_endpoint": "http://127.0.0.1:6231/text2img",
            "astrbot_endpoint": "http://127.0.0.1:6231/text2img",
            "match": True,
            "astrbot_provider": "custom_template",
        },
        "plugin_uptime": 3600,
    }
    html = build_status_card(payload)
    assert "文转图服务状态" in html
    assert "RUNNING" in html
    assert "12" in html
    assert "88.5" in html
    assert "3600" not in html  # 运行时长应该被格式化成人类可读

    lowered = html.lower()
    for marker in EXTERNAL_MARKERS:
        assert marker not in lowered, marker


@pytest.mark.parametrize("payload", [{}, {"server": None, "browser": None}])
def test_status_card_tolerates_missing_sections(payload):
    html = build_status_card(payload)
    assert "文转图服务状态" in html
    assert "STOPPED" in html


def test_status_card_escapes_injected_values():
    html = build_status_card(
        {
            "server": {
                "running": True,
                "endpoint": "<img src=x onerror=alert(1)>",
            },
            "browser": {"installed": False},
            "renderer": {},
            "endpoint": {},
        }
    )
    assert "<img src=x" not in html
