"""AstrBot 文转图模板的清单、预览图与「本插件接管槽位」。

面板的「模板」栏用这里的两件事：

* 列出 AstrBot 能用的模板（内置 + 用户自建），并给每个模板渲染一张预览图，
  让用户直接看到排版效果再决定用哪个；
* 把选中的模板内容写进本插件的专属槽位 ``t2i_service_plugin``。

槽位是这套设计的关键：AstrBot 的 ``t2i_active_template``（模板名）在启动时被流水线
缓存，插件没有重建流水线的入口，改名字必须重启才生效；但模板**内容**是每次渲染现读
的。所以「应用」一律写同一个槽位文件——第一次把 ``t2i_active_template`` 切到槽位要
重启一次，之后换风格只覆盖文件内容，立刻生效。
"""

from __future__ import annotations

import hashlib
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
# 预览图按内容哈希命名，模板一改就换名字；只留最近这么多张，避免越积越多
PREVIEW_KEEP = 24

# 预览用的样例文本：标题 / 列表 / 引用 / 表格 / 代码块 / 行内代码都覆盖到，
# 这样模板之间的排版差异一眼能看出来（模板自己用 marked 渲染 Markdown）
SAMPLE_TEXT = """## 群聊日报 · 2026-11-02

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

# 模板名会被直接拼成文件名，也存在于 AstrBot 的模板清单里，所以只放行这一小撮字符
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


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


class TemplateGallery:
    """模板清单 / 预览图缓存 / 槽位写入。"""

    def __init__(self, data_dir: Path, *, plugin_version: str = "") -> None:
        self.data_dir = Path(data_dir)
        self.preview_dir = self.data_dir / "t2i-previews"
        self.plugin_version = plugin_version

    # ------------------------------------------------------------ 路径与清单

    @property
    def user_dir(self) -> Path:
        return user_template_dir()

    @property
    def builtin_dir(self) -> Path:
        return builtin_template_dir()

    @property
    def slot_path(self) -> Path:
        return self.user_dir / f"{SLOT_NAME}.html"

    def _names(self) -> list[str]:
        """两个目录里所有模板名（不含本插件的槽位本身）。"""
        names: set[str] = set()
        for directory in (self.builtin_dir, self.user_dir):
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.suffix == ".html" and entry.is_file():
                    names.add(entry.stem)
        names.discard(SLOT_NAME)
        return sorted(names)

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
        """单个模板的信息：标题、来源、大小、变量、是否被改过。"""
        content = self.read(name)
        builtin_path = self.builtin_dir / f"{name}.html"
        user_path = self.user_dir / f"{name}.html"
        path = user_path if user_path.is_file() else builtin_path
        try:
            stat = path.stat()
            size, updated = stat.st_size, int(stat.st_mtime)
        except OSError:
            size, updated = len(content.encode("utf-8")), 0
        builtin = builtin_path.is_file()
        modified = False
        if builtin and user_path.is_file():
            try:
                modified = self._hash(self.read(name)) != self._hash_path(builtin_path)
            except OSError:
                modified = False
        return {
            "name": name,
            "title": self._title(content) or name,
            "description": self._description(content),
            "source": "builtin" if builtin else "custom",
            "modified": modified,
            "bytes": size,
            "updated": updated,
            "variables": sorted({match.group(1) for match in _VAR_RE.finditer(content)}),
            "shiki": "shiki_runtime" in content,
        }

    def read(self, name: str) -> str:
        """按名字读模板内容：先用户目录再内置目录。"""
        if not name or ".." in name or "/" in name or "\\" in name:
            raise ValueError("模板名称包含非法字符。")
        for path in (self.user_dir / f"{name}.html", self.builtin_dir / f"{name}.html"):
            if path.is_file():
                return path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"模板不存在：{name}")

    # ------------------------------------------------------------ 新建与编辑

    def create(
        self,
        name: str,
        *,
        source: str = "",
        content: str = "",
    ) -> dict[str, object]:
        """新建一个用户模板：起始内容可以是粘贴的 HTML，也可以复制某个现有模板。"""
        target = self._writable_path(name, must_not_exist=True)
        self._write(target, self._initial_content(source=source, content=content))
        return self.describe(name)

    def save(self, name: str, content: str) -> dict[str, object]:
        """覆盖写用户模板；内置模板被改后用户目录里会多一份副本（AstrBot 用户优先）。"""
        text = (content or "").strip()
        if not text:
            raise ValueError("模板内容不能为空。")
        target = self._writable_path(name, must_not_exist=False)
        if not text.endswith("\n"):
            text += "\n"
        self._write(target, text)
        return self.describe(name)

    def _writable_path(self, name: str, *, must_not_exist: bool) -> Path:
        if not _NAME_RE.fullmatch(name or ""):
            raise ValueError(
                "模板名只能用字母、数字、下划线和短横线，需以字母或数字开头，最长 64 个字符。"
            )
        if name == SLOT_NAME:
            raise ValueError(f"模板名 {SLOT_NAME} 被本插件占用，请换一个。")
        if must_not_exist and (
            (self.user_dir / f"{name}.html").is_file()
            or (self.builtin_dir / f"{name}.html").is_file()
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
                if self._hash(self.read(name)) == digest:
                    return name
            except (OSError, ValueError, FileNotFoundError):
                continue
        return ""

    def apply(self, name: str) -> dict[str, object]:
        """把模板内容写进槽位；返回槽位名、来源与「内容是否有变化」。"""
        content = self.read(name)
        target = self.slot_path
        target.parent.mkdir(parents=True, exist_ok=True)
        changed = self.slot_content() != content
        if changed:
            # 原子替换：AstrBot 可能正好在读这个文件，不能让它读到写了一半的内容
            temp = target.with_name(f"{target.name}.tmp")
            temp.write_text(content, encoding="utf-8")
            temp.replace(target)
        return {"slot": SLOT_NAME, "source": name, "changed": changed}

    # ------------------------------------------------------------ 预览图缓存

    def sample_data(self) -> dict[str, str]:
        """渲染预览时的模板变量，与 AstrBot 真实渲染时给的一致。"""
        return {"text": SAMPLE_TEXT, "version": self.plugin_version or "v0.0.0"}

    def preview_file(self, name: str) -> Path:
        """预览图路径：按模板内容哈希命名，内容没变就一直命中缓存。"""
        return self.preview_dir / f"{self._hash(self.read(name))[:16]}.jpg"

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

    def _description(self, content: str) -> str:
        match = _DESC_RE.search(content)
        return match.group(1).strip() if match else ""

    def _hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _hash_path(self, path: Path) -> str:
        return self._hash(path.read_text(encoding="utf-8"))
