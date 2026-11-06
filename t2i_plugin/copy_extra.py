"""文转图回复的「可复制内容补发」。

AstrBot 把长回复渲染成图片后，图里的链接、代码块、配置行与文件路径都没法长按
复制。这里在落图前（``on_decorating_result``）从即将被渲染的纯文本里挑出这些
片段，等消息真的以图片发出后（``after_message_sent``）再按配置补发一条文本。

挑片段的规则宁缺毋滥：只认一眼就能确定是「拿来复制」的内容，避免把普通句子
也补发一遍刷屏；补发只在确认原消息变成了图片时进行，渲染失败退化成文字时
不补发，否则同一份内容会发两遍。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from astrbot.api.event import (
    AstrMessageEvent,
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.api.message_components import Image, Node, Plain

from t2i_plugin.config import COPY_KINDS, MIN_WORD_THRESHOLD

# 挂在 event extra 上的键：落图前收集、发送后补发
COPY_EXTRA_KEY = "t2i_copy_extra_items"

MAX_ITEMS = 12
MAX_ITEM_CHARS = 600
MAX_TOTAL_CHARS = 1500
MERGE_HEADER = "📋 图片里不便复制的内容："
# 只有 QQ（aiocqhttp）协议端支持合并转发消息，其它平台发普通文本
FORWARD_PLATFORM = "aiocqhttp"
FORWARD_SENDER_NAME = "AstrBot"

# URL 去掉中文标点与成对括号，避免把「（...）」的尾巴一起吃进来
_URL_RE = re.compile(r"https?://[^\s<>\"'`（）()\[\]【】{}，。；、！？]+")
# 代码块只取围栏里的正文；语言名（```yaml）不参与复制
_FENCE_RE = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)
# 形如 KEY=value / key: value / export KEY=value 的配置片段；不要求出现在行首，
# 因为中文叙述里常写成「配置行：t2i_strategy: remote」。
_CONFIG_LINE_RE = re.compile(
    r"(?<![\w.\-])(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_.\-]{0,63})"
    # 值在换行、markdown 反引号、中文标点或汉字处截断：配置值都是 ASCII，
    # 后面接着的往往是「就行」「再重启」这类中文说明
    r"[ \t]*(?:=|:)[ \t]*(\S[^\n`，。；、（）：\u4e00-\u9fff]*)"
)
# 绝对路径：/a/b 至少两级，或 Windows 的 C:\a\b；不匹配 /command 这类单段斜杠
_PATH_RE = re.compile(
    r"(?<![\w/.\-])"
    r"(?:[A-Za-z]:[\\/][^\s\"'`<>|，。；）】]+"
    r"|/(?:[^\s/\"'`<>|，。；）】]+/)+[^\s\"'`<>|，。；）】]+)"
)
# 只去行文标点与 markdown 反引号；括号/引号在 JSON、代码块里有含义，不能乱删
_TRAILING_PUNCT = ".,;:，。；：、`"


def _clean(text: str) -> str:
    return text.strip().strip(_TRAILING_PUNCT).strip()


def _clip(text: str) -> str:
    if len(text) <= MAX_ITEM_CHARS:
        return text
    return text[:MAX_ITEM_CHARS].rstrip() + "…"


def _looks_like_config_key(key: str) -> bool:
    # 普通英文句子（Note: ...）里也可能出现冒号，只有带下划线/点/横线的键才算配置
    return any(mark in key for mark in ("_", ".", "-"))


def _enabled_kinds(kinds: Sequence[str] | None) -> set[str]:
    if kinds is None:
        return set(COPY_KINDS)
    return {kind for kind in kinds if kind in COPY_KINDS}


def extract_copy_items(
    text: str, kinds: Sequence[str] | None = None
) -> list[str]:
    """从一段即将落图的文本里挑出值得复制的片段（保持出现顺序、去重）。

    默认识别四类：链接、代码块、配置行、绝对路径；``kinds`` 可只保留其中几类
    （例如只想补链接就传 ``("links",)``）。被代码块整体包含的片段不再单列。
    """
    enabled = _enabled_kinds(kinds)
    candidates: list[tuple[int, str]] = []
    if "links" in enabled:
        for match in _URL_RE.finditer(text):
            candidates.append((match.start(), _clean(match.group(0))))
    if "code" in enabled:
        for match in _FENCE_RE.finditer(text):
            # 代码块整段保留，末尾的 } 或 " 都是内容，不能按行文标点清掉
            body = match.group(1).strip()
            if body:
                candidates.append((match.start(), _clip(body)))
    if "config" in enabled:
        for match in _CONFIG_LINE_RE.finditer(text):
            if _looks_like_config_key(match.group(1)):
                candidates.append((match.start(), _clean(match.group(0))))
    if "paths" in enabled:
        for match in _PATH_RE.finditer(text):
            candidates.append((match.start(), _clean(match.group(0))))

    candidates.sort(key=lambda item: item[0])
    items: list[str] = []
    total = 0
    for _, raw in candidates:
        item = _clean(raw)
        if not item or len(item) < 3:
            continue
        # 代码块会先被收下，里面的链接/配置行就不再重复补发
        if any(item in kept for kept in items):
            continue
        if len(items) >= MAX_ITEMS or total + len(item) > MAX_TOTAL_CHARS:
            break
        items.append(item)
        total += len(item)
    return items


def format_copy_text(items: Sequence[str]) -> str:
    """把片段排成一条待补发的文本（合并转发里装的就是它）。"""
    return MERGE_HEADER + "\n" + "\n\n".join(items)


def build_copy_chains(
    items: Sequence[str],
    mode: str,
    *,
    self_id: str = "",
    platform_name: str = "",
) -> list[MessageChain]:
    """按配置生成待补发的消息链。

    ``merged`` 在 QQ 上走合并转发消息（``Node``），其它平台没有合并转发，
    退化成一条普通文本；``single`` 是一条普通文本；``separate`` 每项一条；
    ``off`` 不补发。原图片内容始终不动，这里只负责额外那条。
    """
    if mode == "off" or not items:
        return []
    if mode == "separate":
        return [MessageChain([Plain(item)]) for item in items]
    text = format_copy_text(items)
    if mode == "merged" and platform_name == FORWARD_PLATFORM:
        node = Node(
            content=[Plain(text)],
            name=FORWARD_SENDER_NAME,
            uin=self_id or "0",
        )
        return [MessageChain([node])]
    return [MessageChain([Plain(text)])]


def pending_copy_items(
    event: AstrMessageEvent,
    core_config: Mapping[str, object],
    kinds: Sequence[str] | None = None,
) -> list[str]:
    """判断这条结果会不会被 AstrBot 渲染成图片，是则返回值得补发的片段。

    t2i 开关与「开头的连续纯文本」两项与 AstrBot 的 ResultDecorateStage 严格对齐；
    字数只按 AstrBot 的硬下限粗筛，真正的过滤在 after_message_sent 里做（见下）。
    """
    result = event.get_result()
    if result is None or not result.chain:
        return []
    if result.result_content_type in {
        ResultContentType.STREAMING_RESULT,
        ResultContentType.STREAMING_FINISH,
    }:
        return []
    use_t2i = result.use_t2i_
    if use_t2i is None:
        use_t2i = bool(core_config.get("t2i", False))
    if not use_t2i:
        return []

    # AstrBot 只把开头的连续纯文本拿去渲染，遇到非 Plain 段就停
    parts: list[str] = []
    for component in result.chain:
        if not isinstance(component, Plain):
            break
        parts.append(component.text or "")
    plain_text = "".join("\n\n" + part for part in parts)
    # 字数阈值在 AstrBot 启动时被缓存，这里无法反推线上实际生效的值；按 AstrBot
    # 的硬下限判断即可——多提取几条只是白做一次正则，是否补发由 after_message_sent
    # 里「结果确实是图片」来兜底，不会多发。
    if not plain_text or len(plain_text) <= MIN_WORD_THRESHOLD:
        return []
    return extract_copy_items(plain_text, kinds)


def rendered_as_image(result: MessageEventResult | None) -> bool:
    """原消息最终是不是被替换成了单张图片（渲染失败时仍是文字，不补发）。"""
    if result is None or len(result.chain) != 1:
        return False
    return isinstance(result.chain[0], Image)
