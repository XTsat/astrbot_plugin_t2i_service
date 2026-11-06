"""可复制内容补发的提取规则与发送判定。"""

from __future__ import annotations

from astrbot.api.event import MessageEventResult, ResultContentType
from astrbot.api.message_components import Image, Node, Plain

from t2i_plugin.copy_extra import (
    COPY_EXTRA_KEY,
    MAX_ITEMS,
    build_copy_chains,
    extract_copy_items,
    format_copy_text,
    pending_copy_items,
    rendered_as_image,
)


class FakeEvent:
    """只实现钩子用到的 event 接口。"""

    def __init__(self, result: MessageEventResult | None) -> None:
        self.result = result
        self.extras: dict[str, object] = {}
        self.sent: list = []

    def get_result(self) -> MessageEventResult | None:
        return self.result

    def set_extra(self, key, value) -> None:
        self.extras[key] = value

    def get_extra(self, key: str | None = None, default=None):
        if key is None:
            return self.extras
        return self.extras.get(key, default)

    async def send(self, chain) -> None:
        self.sent.append(chain)


def _result(*components, use_t2i: bool | None = None) -> MessageEventResult:
    result = MessageEventResult(chain=list(components))
    result.use_t2i_ = use_t2i
    return result


# ---------------------------------------------------------------- 提取规则


def test_extracts_links_config_lines_code_and_paths():
    text = (
        "服务地址：http://127.0.0.1:8999/text2img\n"
        "配置行：t2i_strategy: remote\n"
        "浏览器目录：/opt/AstrBot/data/plugins/browsers\n"
        "代码块：\n"
        "```yaml\n"
        "t2i: true\n"
        "```"
    )
    items = extract_copy_items(text)
    assert "http://127.0.0.1:8999/text2img" in items
    assert "t2i_strategy: remote" in items
    assert "/opt/AstrBot/data/plugins/browsers" in items
    # 代码块整体收下，块里的配置行不再单列（避免同一内容发两遍）
    assert "t2i: true" in items
    assert len(items) == 4


def test_plain_english_sentences_are_not_config_lines():
    assert extract_copy_items("Note: this is just a sentence, nothing to copy.") == []


def test_dates_and_single_segment_commands_are_not_paths():
    assert extract_copy_items("版本 2026/11/06 发布，发送 /t2i_install 安装。") == []


def test_windows_absolute_path_is_detected():
    items = extract_copy_items(r"配置文件在 C:\AstrBot\data\config\astrbot_config.json")
    assert items == [r"C:\AstrBot\data\config\astrbot_config.json"]


def test_items_inside_code_fence_are_not_duplicated():
    text = "```\ncurl http://example.com/file\n```\n也可以直接打开 http://example.com/file"
    assert extract_copy_items(text) == ["curl http://example.com/file"]


_MIXED_TEXT = (
    "先看 https://a.example/doc\n"
    "```\nport: 8080\n```\n"
    "配置 t2i_endpoint: http://b.example/text2img\n"
    "路径 /opt/AstrBot/data/logs\n"
)


def test_kinds_can_be_limited_to_links_only():
    assert extract_copy_items(_MIXED_TEXT, ("links",)) == [
        "https://a.example/doc",
        "http://b.example/text2img",
    ]


def test_kinds_can_be_limited_to_code_only():
    assert extract_copy_items(_MIXED_TEXT, ("code",)) == ["port: 8080"]


def test_kinds_can_be_limited_to_config_and_paths():
    assert extract_copy_items(_MIXED_TEXT, ("config", "paths")) == [
        "t2i_endpoint: http://b.example/text2img",
        "/opt/AstrBot/data/logs",
    ]


def test_empty_kinds_extracts_nothing():
    assert extract_copy_items(_MIXED_TEXT, ()) == []


def test_items_are_capped():
    text = "\n".join(f"http://example.com/{index}" for index in range(MAX_ITEMS + 8))
    assert len(extract_copy_items(text)) == MAX_ITEMS


# ---------------------------------------------------------------- 消息排版


def test_merged_mode_uses_qq_forward_message_on_aiocqhttp():
    chains = build_copy_chains(
        ["https://a.example", "t2i: true"],
        "merged",
        self_id="123456",
        platform_name="aiocqhttp",
    )
    assert len(chains) == 1
    node = chains[0].chain[0]
    assert isinstance(node, Node)
    assert node.uin == "123456"
    assert node.name == "AstrBot"
    text = "".join(part.text for part in node.content if isinstance(part, Plain))
    assert "https://a.example" in text and "t2i: true" in text


def test_merged_mode_falls_back_to_plain_text_elsewhere():
    # 其它平台没有合并转发，发一条普通文本即可
    chains = build_copy_chains(["https://a.example"], "merged", platform_name="webchat")
    assert len(chains) == 1
    assert isinstance(chains[0].chain[0], Plain)
    assert "https://a.example" in chains[0].chain[0].text


def test_single_mode_is_one_normal_text_message():
    # 「不合并」：全部内容合在一条普通文本里，但不用 QQ 合并转发
    chains = build_copy_chains(
        ["https://a.example", "t2i: true"], "single", platform_name="aiocqhttp"
    )
    assert len(chains) == 1
    plain = chains[0].chain[0]
    assert isinstance(plain, Plain)
    assert "https://a.example" in plain.text and "t2i: true" in plain.text


def test_separate_mode_sends_one_message_per_item():
    chains = build_copy_chains(["a.example", "b.example"], "separate")
    assert [chain.chain[0].text for chain in chains] == ["a.example", "b.example"]


def test_off_mode_and_empty_items_send_nothing():
    assert build_copy_chains(["a"], "off") == []
    assert build_copy_chains([], "merged") == []


def test_format_copy_text_has_a_short_header():
    text = format_copy_text(["https://a.example"])
    assert text.startswith("📋")
    assert "https://a.example" in text


# ------------------------------------------------------ 何时收集 / 何时补发


CORE_ON = {"t2i": True, "t2i_word_threshold": 150}
CORE_OFF = {"t2i": False, "t2i_word_threshold": 150}
LONG_TEXT = "这里是一段很长的回复。" * 20 + "\n下载链接：https://example.com/file.zip"


def test_collects_items_when_text_will_be_rendered():
    event = FakeEvent(_result(Plain(LONG_TEXT)))
    items = pending_copy_items(event, CORE_ON)
    assert "https://example.com/file.zip" in items


def test_pending_items_respect_selected_kinds():
    event = FakeEvent(_result(Plain(LONG_TEXT)))
    # LONG_TEXT 里只有链接，没有代码块/配置行/路径
    assert pending_copy_items(event, CORE_ON, ("code",)) == []
    assert pending_copy_items(event, CORE_ON, ("links",))


def test_collects_nothing_when_t2i_is_off():
    event = FakeEvent(_result(Plain(LONG_TEXT)))
    assert pending_copy_items(event, CORE_OFF) == []


def test_explicit_use_t2i_overrides_core_switch():
    event = FakeEvent(_result(Plain(LONG_TEXT), use_t2i=True))
    assert pending_copy_items(event, CORE_OFF)
    event = FakeEvent(_result(Plain(LONG_TEXT), use_t2i=False))
    assert pending_copy_items(event, CORE_ON) == []


def test_short_reply_is_never_rendered():
    event = FakeEvent(_result(Plain("链接：https://example.com/x")))
    assert pending_copy_items(event, CORE_ON) == []


def test_streaming_results_are_skipped():
    result = _result(Plain(LONG_TEXT))
    result.result_content_type = ResultContentType.STREAMING_FINISH
    event = FakeEvent(result)
    assert pending_copy_items(event, CORE_ON) == []


def test_only_leading_plain_components_are_rendered():
    result = _result(Plain("开场"), Image.fromURL("https://example.com/a.png"))
    # 第二个组件不是 Plain，AstrBot 不会把它渲染进图片，整条也不会走文转图
    assert pending_copy_items(FakeEvent(result), CORE_ON) == []


def test_rendered_as_image_detects_the_replaced_chain():
    assert rendered_as_image(_result(Image.fromURL("https://example.com/a.png")))
    assert not rendered_as_image(_result(Plain("文字")))
    assert not rendered_as_image(None)
    assert not rendered_as_image(
        _result(Image.fromURL("https://example.com/a.png"), Plain("文字"))
    )


def test_extra_key_is_stable():
    # 钩子之间靠这个键传递片段，改动会让两个时机对不上
    assert COPY_EXTRA_KEY


def test_code_fence_keeps_trailing_braces():
    # 代码块里的 } / " 是内容的一部分，不能按行文标点清掉
    text = '```jsonc\n{\n  "t2i": true\n}\n```'
    assert extract_copy_items(text) == ['{\n  "t2i": true\n}']


def test_markdown_backtick_around_config_value_is_trimmed():
    items = extract_copy_items("把 t2i_endpoint 填成 `t2i_endpoint: http://x.example` 就行")
    # URL 与配置片段只保留一份，且不带 markdown 反引号
    assert items == ["t2i_endpoint: http://x.example"]
