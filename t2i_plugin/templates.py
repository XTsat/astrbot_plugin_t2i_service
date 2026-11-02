"""AstrBot 文转图模板的清单、预览图与「本插件接管槽位」。

面板的「模板」栏用这里的两件事：

* 列出 AstrBot 能用的模板（AstrBot 内置 + 插件自带 + 用户自建），并给每个模板渲染
  一张预览图，让用户直接看到排版效果再决定用哪个；
* 把选中的模板内容写进本插件的专属槽位 ``t2i_service_plugin``。

槽位是这套设计的关键：AstrBot 的 ``t2i_active_template``（模板名）在启动时被流水线
缓存，插件没有重建流水线的入口，改名字必须重启才生效；但模板**内容**是每次渲染现读
的。所以「应用」一律写同一个槽位文件——第一次把 ``t2i_active_template`` 切到槽位要
重启一次，之后换风格只覆盖文件内容，立刻生效。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from pathlib import Path

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

try:  # 老版本 AstrBot 的路径助手不一定齐全，缺哪个退回哪一个
    from astrbot.core.utils import astrbot_path as _astrbot_path
except Exception:  # noqa: BLE001
    _astrbot_path = None  # type: ignore[assignment]

# 本插件在 AstrBot 模板清单里占用的槽位名（文件名即 <名字>.html）
SLOT_NAME = "t2i_service_plugin"
# 模板 → 它想用的字体预设（键在 t2i_plugin/fonts.py 的 FONT_TEMPLATES 里）：
# 面板应用模板时会先看这套字体装没装，没装就提示可以顺手装一下
FONT_PRESET_BY_TEMPLATE = {
    "atrifeed_diary": "diary_card",
    "atrifeed_diary_legacy": "diary_card",
}
# 随插件发布的模板目录：插件目录下的子目录，用户不加自己的模板也有几套可选
PLUGIN_TEMPLATE_SUBDIR = "plugin_templates"
# 模板里按 assets/xxx 引用同目录 assets/ 下的资源（图片、随模板走的脚本），读模板时内联成
# data URI：渲染跑的是 file:// 临时文件，相对路径指不到插件目录，资源只能跟着 HTML 走
_ASSET_REF_RE = re.compile(r"assets/([A-Za-z0-9._-]+)")
# 插件自带模板里的「数据刷新间隔」标记：读模板时换成配置里的分钟数
_REFRESH_RE = re.compile(r"var REFRESH_MINUTES = \d+;")
_ASSET_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    # 正文 Markdown 渲染器随模板走（服务器上通常没有外网，CDN 取不到就静默失败）；
    # 声明成 text/javascript，浏览器才肯执行 data URI 里的脚本
    ".js": "text/javascript",
}
# 预览图按内容哈希命名，模板一改就换名字；只留最近这么多张，避免越积越多
PREVIEW_KEEP = 24

# 预览用的默认样例文本：标题 / 列表 / 引用 / 表格 / 代码块 / 行内代码都覆盖到，
# 这样模板之间的排版差异一眼能看出来（模板自己用 marked 渲染 Markdown）。
# 面板里可以改成自己的文字，改过之后存在 _SAMPLE_FILE 里，这里的默认值不再生效。
DEFAULT_SAMPLE_TEXT = """## 群聊日报 · 2026-11-02

今天群里最热闹的三件事：

1. **新版本发布**：`v1.2.1` 修好了模板预览
2. 周末团建地点投票 → [投票清单](https://example.com/vote)
3. 有人问：「这个插件能吃吗？」

> 引用一条消息：以上统计来自最近 24 小时。

| 成员 | 发言数 | 占比 |
| --- | --- | --- |
| 小明 | 128 | 42% |
| 小红 | 96 | 31% |

```python
from astrbot.api import logger

logger.info("hello t2i")
```

收尾一句：*渲染由文转图服务插件提供*。"""

# 新建模板时的起始内容：一个能直接出图的骨架——标题、正文、代码块、表格都排好，
# 用户只要改里面的样式或结构就能得到自己的风格。
BLANK_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>新模板 {{ version }}</title>
  <meta name="description" content="在面板里新建的模板，改这里就能改样子。">
  <style>
    :root {
      --paper: #fbfbfa;
      --ink: #202224;
      --muted: #6b7075;
      --line: #e2e5e7;
      --fill: #f4f5f5;
      --accent: #2f86bd;
    }

    *,
    *::before,
    *::after {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      padding: 36px 32px 48px;
      overflow-wrap: break-word;
      color: var(--ink);
      background: var(--paper);
      font-family: "Noto Sans CJK SC", "Microsoft YaHei", -apple-system, "Segoe UI", Arial, sans-serif;
      font-size: 24px;
      line-height: 1.72;
    }

    .sheet {
      width: min(100%, 880px);
      margin: 0 auto;
    }

    h1,
    h2,
    h3 {
      margin: 1.2em 0 0.5em;
      line-height: 1.3;
    }

    h1 {
      margin-top: 0;
      font-size: 1.5em;
    }

    h2 {
      font-size: 1.25em;
    }

    p,
    ul,
    ol,
    blockquote,
    table,
    pre {
      margin: 0.8em 0;
    }

    a {
      color: var(--accent);
    }

    blockquote {
      margin-left: 0;
      padding: 0.2em 1em;
      color: var(--muted);
      border-left: 3px solid var(--line);
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92em;
    }

    th,
    td {
      padding: 0.45em 0.6em;
      text-align: left;
      border: 1px solid var(--line);
    }

    th {
      background: var(--fill);
    }

    code {
      padding: 0.12em 0.35em;
      border-radius: 4px;
      background: var(--fill);
      font-size: 0.9em;
    }

    pre {
      padding: 1em;
      overflow-x: auto;
      border-radius: 8px;
      background: var(--fill);
    }

    pre code {
      padding: 0;
      background: none;
    }

    img {
      max-width: 100%;
    }
  </style>
</head>
<body>
  <article class="sheet" id="content"></article>
  <textarea id="markdown-source" hidden>{{ text | safe }}</textarea>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script>
    (function () {
      var source = document.getElementById("markdown-source").value;
      var target = document.getElementById("content");

      // marked 走 CDN，群里没网时解析不出来，这时退回纯文本，至少内容还在
      if (window.marked && typeof window.marked.parse === "function") {
        target.innerHTML = window.marked.parse(source);
        if (window.AstrBotT2IShiki) {
          window.AstrBotT2IShiki.highlightAllCodeBlocks(target, "github-light");
        }
      } else {
        target.textContent = source;
      }
    })();
  </script>
</body>
</html>
"""

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_DESC_RE = re.compile(
    r"""<meta[^>]+name=["']description["'][^>]*content=["'](.*?)["']""", re.S | re.I
)
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_]\w*)")
_ORIGIN_RE = re.compile(
    r"""<meta[^>]+name=["']origin["'][^>]*content=["'](.*?)["']""", re.S | re.I
)
# 出处链接：面板把那行出处渲染成可点的链接，而不是纯文字
_ORIGIN_URL_RE = re.compile(
    r"""<meta[^>]+name=["']origin-url["'][^>]*content=["'](.*?)["']""", re.S | re.I
)
# 单个模板可能带自己的许可证（例如借来的版式按 AGPL-3.0 分发），面板上要能标出来
_LICENSE_RE = re.compile(
    r"""<meta[^>]+name=["']license["'][^>]*content=["'](.*?)["']""", re.S | re.I
)

# font-family 声明里的字体名：面板要据此告诉用户「这套模板原本想用什么字体」
_FONT_DECL_RE = re.compile(r"font-family:\s*([^;}]+);", re.I)
_FONT_NAME_RE = re.compile(r'"([^"]+)"')

# 模板名会被直接拼成文件名，也存在于 AstrBot 的模板清单里，所以只放行这一小撮字符
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

# 面板里改过的预览样例文本放这里；文件不存在/内容为空表示「用内置默认」
_SAMPLE_FILE = "t2i-preview-sample.md"
# 样例文本只给人看排版，几千字足够；挡住误贴整篇文章把预览请求撑爆
MAX_SAMPLE_CHARS = 16 * 1024

# 面板上的显示名单独存一份：模板文件名是标识（AstrBot 配置里指的就是它），
# 改文件名会连带把已应用的设置指空，所以显示名不能写进文件名。
_TITLES_FILE = "_titles.json"
_TITLE_LIMIT = 64


def guard_name(name: str) -> str:
    """读/删只要求「不会跳出模板目录」；新建时的字符限制另有一套（_NAME_RE）。"""
    text = (name or "").strip()
    if not text or ".." in text or "/" in text or "\\" in text:
        raise ValueError("模板名称包含非法字符。")
    return text


def _clean_title(value: str) -> str:
    """显示名允许中文与空格，只挡掉换行和控制字符——它会被塞进 HTML 文本节点。"""
    text = " ".join((value or "").split())
    if len(text) > _TITLE_LIMIT:
        raise ValueError(f"显示名称最长 {_TITLE_LIMIT} 个字符。")
    return text


def _path_helper(name: str) -> Path | None:
    """调用 AstrBot 的路径助手；不存在或抛异常都返回 None。"""
    helper = getattr(_astrbot_path, name, None) if _astrbot_path else None
    if not callable(helper):
        return None
    try:
        return Path(helper())
    except Exception:  # noqa: BLE001 - 路径助手异常不该让页面白屏
        return None


def user_template_dir() -> Path:
    """AstrBot 的用户模板目录：内置模板被复制到这里，同名覆盖内置。"""
    for helper, suffix in (
        ("get_astrbot_t2i_templates_path", ()),
        ("get_astrbot_data_path", ("t2i_templates",)),
    ):
        base = _path_helper(helper)
        if base is not None:
            return base.joinpath(*suffix)
    # 最老版本连数据目录助手都没有：插件数据目录在 <data>/plugin_data/<插件名> 下
    return Path(get_astrbot_plugin_data_path()).parent.parent / "t2i_templates"


def builtin_template_dir() -> Path:
    """AstrBot 源码内置的模板目录（base / astrbot_powershell / astrbot_vitepress）。"""
    try:
        import astrbot

        return Path(astrbot.__file__).resolve().parent / "core/utils/t2i/template"
    except Exception:  # noqa: BLE001 - 拿不到源码目录时就只认用户目录
        return user_template_dir()


def plugin_template_dir() -> Path:
    """本插件随包发布的模板目录（面板里来源显示为「插件」）。

    跟着插件目录走而不是数据目录：卸载重装时随插件一起更新，也不占用户数据空间。
    """
    return Path(__file__).resolve().parent / PLUGIN_TEMPLATE_SUBDIR


def _font_families(content: str) -> list[str]:
    """模板里 font-family 写到的字体名（按出现顺序去重）。"""
    names: list[str] = []
    for declaration in _FONT_DECL_RE.findall(content):
        for name in _FONT_NAME_RE.findall(declaration):
            if name not in names:
                names.append(name)
    return names


class TemplateGallery:
    """模板清单 / 预览图缓存 / 槽位写入。"""

    def __init__(
        self, data_dir: Path, *, plugin_version: str = "", refresh_minutes: int = 15
    ) -> None:
        self.data_dir = Path(data_dir)
        self.preview_dir = self.data_dir / "t2i-previews"
        self.plugin_version = plugin_version
        # 插件自带模板里天气 / 心情 / 台词的换新间隔（分钟），读取模板时写进 HTML
        self.refresh_minutes = max(1, refresh_minutes)
        # assets/ 图片的 data URI 缓存：key 是文件路径，值带 mtime/大小用于失效判断
        self._asset_cache: dict[Path, tuple[float, int, str]] = {}

    # ------------------------------------------------------------ 路径与清单

    @property
    def user_dir(self) -> Path:
        return user_template_dir()

    @property
    def builtin_dir(self) -> Path:
        return builtin_template_dir()

    @property
    def plugin_dir(self) -> Path:
        return plugin_template_dir()

    @property
    def assets_dir(self) -> Path:
        """随插件发布的模板资源目录（模板里写 assets/xxx.jpg、assets/xxx.js 即可）。"""
        return self.plugin_dir / "assets"

    @property
    def slot_path(self) -> Path:
        return self.user_dir / f"{SLOT_NAME}.html"

    @property
    def titles_path(self) -> Path:
        return self.user_dir / _TITLES_FILE

    def _names(self) -> list[str]:
        """三处目录里所有模板名（不含本插件的槽位本身）。"""
        names: set[str] = set()
        for directory in (self.builtin_dir, self.plugin_dir, self.user_dir):
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.suffix == ".html" and entry.is_file():
                    names.add(entry.stem)
        names.discard(SLOT_NAME)
        return sorted(names)

    def _shipped(self, name: str) -> Path | None:
        """自带版本的文件：插件自带的优先于 AstrBot 内置的，两处都没有返回 None。"""
        for path in (
            self.plugin_dir / f"{name}.html",
            self.builtin_dir / f"{name}.html",
        ):
            if path.is_file():
                return path
        return None

    def _origin(self, name: str) -> tuple[str, Path | None]:
        """模板来源与对应的自带文件；("custom", None) 表示纯用户自建。"""
        shipped = self._shipped(name)
        if shipped is None:
            return "custom", None
        if shipped == self.plugin_dir / f"{name}.html":
            return "plugin", shipped
        return "builtin", shipped

    def list_templates(self) -> list[dict[str, object]]:
        """面板用的模板清单，标出槽位当前装的是哪一个。"""
        applied = self.applied_source()
        items: list[dict[str, object]] = []
        for name in self._names():
            try:
                item = self.describe(name)
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                logger.warning(f"[t2i] 读取模板 {name} 失败：{exc}")
                continue
            item["applied"] = name == applied
            items.append(item)
        return items

    def describe(self, name: str) -> dict[str, object]:
        """单个模板的信息：标题、来源、大小、变量、是否被改过。

        来源看的是「自带版本在哪一层」，不是「内容从哪个文件读到」：用户改了插件模板后
        副本落在用户目录，但它仍然是那套插件模板，不能被报成自建模板。
        """
        content = self.read(name)
        source, shipped = self._origin(name)
        user_path = self.user_dir / f"{name}.html"
        path = user_path if user_path.is_file() else shipped
        size, updated = len(content.encode("utf-8")), 0
        if path is not None:
            try:
                stat = path.stat()
                size, updated = stat.st_size, int(stat.st_mtime)
            except OSError:
                pass
        modified = False
        if shipped is not None and user_path.is_file():
            try:
                modified = self._hash(content) != self._hash_path(shipped)
            except OSError:
                modified = False
        custom_title = self._custom_titles().get(name, "")
        return {
            "name": name,
            "title": custom_title or self._title(content) or name,
            "custom_title": bool(custom_title),
            "description": self._description(content),
            "credit": self._credit(content),
            "credit_url": self._credit_url(content),
            "license": self._license(content),
            "source": source,
            "modified": modified,
            "bytes": size,
            "updated": updated,
            "font_families": _font_families(content),
            "variables": sorted({match.group(1) for match in _VAR_RE.finditer(content)}),
            "shiki": "shiki_runtime" in content,
            "font_preset": FONT_PRESET_BY_TEMPLATE.get(name, ""),
        }

    def read(self, name: str) -> str:
        """按名字读模板原文：用户目录 > 插件自带 > AstrBot 内置。

        给的是**文件里的原样内容**（图片仍是 ``assets/xxx`` 引用）：面板编辑器需要看到
        能改的引用；交给渲染之前再过一遍 :meth:`render_text`。
        """
        guard_name(name)
        for path in (
            self.user_dir / f"{name}.html",
            self.plugin_dir / f"{name}.html",
            self.builtin_dir / f"{name}.html",
        ):
            if path.is_file():
                return path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"模板不存在：{name}")

    def render_text(self, name: str) -> str:
        """渲染用的内容：把 ``assets/xxx`` 换成图片本身。

        渲染跑的是 ``file://`` 临时文件，相对路径指不到插件目录，图片只能跟着 HTML 走。
        """
        return self.apply_refresh_minutes(self.inline_assets(self.read(name)))

    def apply_refresh_minutes(self, text: str) -> str:
        """把模板里的刷新间隔标记换成配置值。

        模板不能靠 Jinja 变量拿这个值——AstrBot 真实渲染只给 text / version，
        所以在读模板时就把数字写进 HTML，预览、应用、真实出图三处口径一致。
        """
        return _REFRESH_RE.sub(f"var REFRESH_MINUTES = {self.refresh_minutes};", text)

    def inline_assets(self, text: str) -> str:
        """把模板里的 assets/xxx 引用换成 data URI；找不到的图保持原样（至少不炸）。"""

        def replace(match: re.Match[str]) -> str:
            uri = self._asset_data_uri(match.group(1))
            return uri or match.group(0)

        return _ASSET_REF_RE.sub(replace, text)

    def _asset_data_uri(self, name: str) -> str:
        """读一个模板资源（图片 / 脚本）并编码；按 mtime + 大小缓存，换文件后自动失效。"""
        for base in (self.assets_dir, self.user_dir / "assets"):
            path = base / name
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file():
                continue
            cached = self._asset_cache.get(path)
            if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                return cached[2]
            mime = _ASSET_MIME.get(path.suffix.lower(), "application/octet-stream")
            try:
                payload = path.read_bytes()
            except OSError as exc:
                logger.warning(f"[t2i] 读取模板资源失败 {path}：{exc}")
                continue
            uri = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
            self._asset_cache[path] = (stat.st_mtime, stat.st_size, uri)
            return uri
        logger.warning(f"[t2i] 模板引用的资源不存在：assets/{name}")
        return ""

    # ------------------------------------------------------------ 新建与编辑

    def create(
        self,
        name: str,
        *,
        source: str = "",
        content: str = "",
        title: str = "",
    ) -> dict[str, object]:
        """新建一个用户模板：起始内容可以是粘贴的 HTML，也可以复制某个现有模板。"""
        target = self._writable_path(name, must_not_exist=True)
        self._write(target, self._initial_content(source=source, content=content))
        self._store_title(name, title)
        return self.describe(name)

    def save(self, name: str, content: str, *, title: str | None = None) -> dict[str, object]:
        """覆盖写用户模板；内置模板被改后用户目录里会多一份副本（AstrBot 用户优先）。"""
        text = (content or "").strip()
        if not text:
            raise ValueError("模板内容不能为空。")
        target = self._writable_path(name, must_not_exist=False)
        if not text.endswith("\n"):
            text += "\n"
        self._write(target, text)
        if title is not None:
            self._store_title(name, title)
        return self.describe(name)

    def delete(self, name: str) -> dict[str, object]:
        """删掉用户目录里的模板；自带模板只能删用户副本，删完自带版本又露出来。"""
        guard_name(name)
        if name == SLOT_NAME:
            raise ValueError("该名字被本插件占用，不能删除。")
        target = self.user_dir / f"{name}.html"
        if not target.is_file():
            shipped = self._shipped(name)
            if shipped is not None:
                if shipped == self.plugin_dir / f"{name}.html":
                    raise ValueError("插件自带模板不能删除。")
                raise ValueError("内置模板不能删除。")
            raise FileNotFoundError(f"模板不存在：{name}")
        target.unlink()
        self._store_title(name, "")
        self._drop_preview(name)
        return {
            "name": name,
            "deleted": True,
            "shadowed": self._shipped(name) is not None,
        }

    def _store_title(self, name: str, title: str) -> None:
        """写显示名；空串表示删掉记录，回落到模板自带的 <title>。"""
        clean = _clean_title(title)
        titles = self._custom_titles()
        if clean:
            titles[name] = clean
        elif name in titles:
            titles.pop(name)
        else:
            return
        self._titles_write(titles)

    def _custom_titles(self) -> dict[str, str]:
        try:
            raw = self.titles_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items() if str(value).strip()}

    def _titles_write(self, titles: dict[str, str]) -> None:
        if not titles:
            try:
                self.titles_path.unlink()
            except OSError:
                pass
            return
        self._write(self.titles_path, json.dumps(titles, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def _writable_path(self, name: str, *, must_not_exist: bool) -> Path:
        if not _NAME_RE.fullmatch(name or ""):
            raise ValueError(
                "模板名只能用字母、数字、下划线和短横线，需以字母或数字开头，最长 64 个字符。"
            )
        if name == SLOT_NAME:
            raise ValueError(f"模板名 {SLOT_NAME} 被本插件占用，请换一个。")
        if must_not_exist and (
            self._shipped(name) is not None or (self.user_dir / f"{name}.html").is_file()
        ):
            raise FileExistsError(f"模板已存在：{name}")
        return self.user_dir / f"{name}.html"

    def _initial_content(self, *, source: str, content: str) -> str:
        pasted = (content or "").strip()
        if pasted:
            return pasted if pasted.endswith("\n") else pasted + "\n"
        copied = (source or "").strip()
        if copied:
            return self.read(copied)
        return BLANK_TEMPLATE

    def _write(self, target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        # 原子替换：AstrBot 或预览渲染可能正好在读这个文件，不能让它读到半截内容
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(target)

    # ------------------------------------------------------------ 槽位

    def slot_content(self) -> str | None:
        try:
            return self.slot_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def applied_source(self) -> str:
        """槽位里现在装的是哪个模板的内容（按内容比对，没有记录也不怕被手改）。"""
        slot = self.slot_content()
        if slot is None:
            return ""
        digest = self._hash(slot)
        for name in self._names():
            try:
                if self._hash(self.render_text(name)) == digest:
                    return name
            except (OSError, ValueError, FileNotFoundError):
                continue
        return ""

    def apply(self, name: str) -> dict[str, object]:
        """把模板内容写进槽位；返回槽位名、来源与「内容是否有变化」。"""
        content = self.render_text(name)
        target = self.slot_path
        target.parent.mkdir(parents=True, exist_ok=True)
        changed = self.slot_content() != content
        if changed:
            # 原子替换：AstrBot 可能正好在读这个文件，不能让它读到写了一半的内容
            temp = target.with_name(f"{target.name}.tmp")
            temp.write_text(content, encoding="utf-8")
            temp.replace(target)
        return {"slot": SLOT_NAME, "source": name, "changed": changed}

    # ------------------------------------------------------------ 预览样例文本

    @property
    def sample_text_path(self) -> Path:
        return self.data_dir / _SAMPLE_FILE

    def _saved_sample_text(self) -> str | None:
        """用户改过的样例文本；文件不存在、只有空白或读失败都算没改过。

        写文件时会补一个换行结尾，读回来统一去掉首尾空白：面板文本框里不该多一个空行，
        Markdown 样例的排版也不受这点空白影响。
        """
        try:
            text = self.sample_text_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return text.strip() or None

    def sample_text(self) -> str:
        """当前预览用的样例文本：改过就用用户的，否则用内置默认。"""
        return self._saved_sample_text() or DEFAULT_SAMPLE_TEXT

    def sample_payload(self) -> dict[str, object]:
        """给面板的状态：当前值、默认值、是否被改过（「恢复默认」按钮据此置灰）。"""
        saved = self._saved_sample_text()
        return {
            "text": saved or DEFAULT_SAMPLE_TEXT,
            "default": DEFAULT_SAMPLE_TEXT,
            "custom": saved is not None,
        }

    def save_sample_text(self, text: str) -> dict[str, object]:
        """存下用户改的样例文本；内容为空说明他想清空，直接拒绝并让他点「恢复默认」。"""
        value = (text or "").strip()
        if not value:
            raise ValueError("样例文本不能为空，想用回默认就点「恢复默认」")
        if len(value) > MAX_SAMPLE_CHARS:
            raise ValueError(f"样例文本太长（超过 {MAX_SAMPLE_CHARS // 1024} KB）")
        self._write(self.sample_text_path, value + "\n")
        return self.sample_payload()

    def reset_sample_text(self) -> dict[str, object]:
        """删掉自定义文件即可回到内置默认；本来就没有也不算错。"""
        try:
            self.sample_text_path.unlink()
        except OSError:
            pass
        return self.sample_payload()

    # ------------------------------------------------------------ 预览图缓存

    def sample_data(self) -> dict[str, str]:
        """渲染预览时的模板变量。

        比 AstrBot 真实渲染多一个 ``preview``：插件自带模板用它区分「面板预览」与
        「真实出图」——预览每次都换一档随机天气，真实出图按天固定。多给的这个变量
        对别的模板没有副作用（不读它的模板直接用不到）。
        """
        return {
            "text": self.sample_text(),
            "version": self.plugin_version or "v0.0.0",
            "preview": "1",
        }

    def preview_file(self, name: str) -> Path:
        """预览图路径：模板内容 + 当前样例文本一起参与哈希。

        只哈希模板会让「改了样例文本、卡片还是旧文字」这种局面出现，而预览图恰恰是
        照着样例文本渲的，所以两样任一改动都得换文件名重渲。
        """
        digest = self._hash(self.render_text(name) + "\x00" + self.sample_text())
        return self.preview_dir / f"{digest[:16]}.jpg"

    def cached_preview(self, name: str) -> Path | None:
        try:
            path = self.preview_file(name)
        except (OSError, ValueError, FileNotFoundError):
            return None
        try:
            return path if path.is_file() and path.stat().st_size > 0 else None
        except OSError:
            return None

    def store_preview(self, name: str, rendered: Path) -> Path:
        """把刚渲染好的图收进缓存目录，顺带清掉太老的。"""
        target = self.preview_file(name)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rendered, target)
        self._prune_previews()
        return target

    def _drop_preview(self, name: str) -> None:
        try:
            self.preview_file(name).unlink()
        except OSError:
            pass

    def _prune_previews(self) -> None:
        try:
            files = sorted(
                self.preview_dir.glob("*.jpg"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for stale in files[PREVIEW_KEEP:]:
            try:
                stale.unlink()
            except OSError:
                continue

    # ------------------------------------------------------------ 解析工具

    def _title(self, content: str) -> str:
        match = _TITLE_RE.search(content)
        if not match:
            return ""
        # 标题里常带 {{ version }} 这类模板变量，展示时不需要
        return re.sub(r"\{\{.*?\}\}", "", match.group(1)).strip()

    def _credit(self, content: str) -> str:
        """模板出处（面板在「模板变量」下面显示一行，方便指回原作者）。"""
        match = _ORIGIN_RE.search(content)
        return match.group(1).strip() if match else ""

    def _credit_url(self, content: str) -> str:
        """出处链接；只放行 http(s)，别把 javascript: 之类塞进面板。"""
        match = _ORIGIN_URL_RE.search(content)
        if not match:
            return ""
        url = match.group(1).strip()
        return url if url.startswith(("http://", "https://")) else ""

    def _license(self, content: str) -> str:
        """模板自己声明的许可证；没有声明就是空串（跟插件本体一样按 Apache-2.0）。"""
        match = _LICENSE_RE.search(content)
        return match.group(1).strip() if match else ""

    def _description(self, content: str) -> str:
        match = _DESC_RE.search(content)
        return match.group(1).strip() if match else ""

    def _hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _hash_path(self, path: Path) -> str:
        return self._hash(path.read_text(encoding="utf-8"))
