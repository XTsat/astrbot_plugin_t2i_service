"""字体下载、安装与字体目录供给（不改动渲染链路）。

渲染仍然是「HTML → Chromium 截图」，本模块只做两件事：

* 把字体文件放到插件数据目录的 ``fonts/`` 下（下载、链接下载、上传三条路径）；
* 生成一份 fontconfig 配置，通过 ``FONTCONFIG_FILE`` 把该目录挂进 Chromium
  的字体查找路径，模板里写的 ``font-family`` 因此能命中新装的字体。

字体清单对齐 ``astrbot_plugin_qq_group_daily_analysis`` 的
``docs/T2I_FONTS.md``：按模板分组（一键下载整套），也支持单家族下载、任意
链接下载与手动上传。中文模板用到的字体体积较大（Noto CJK 单个 10–25 MB），
因此下载放在后台任务里跑，页面轮询进度。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from t2i_plugin.browser import DONE, FAILED, IDLE, RUNNING

from astrbot.api import logger


# ---------------------------------------------------------------- 常量

FONT_SUFFIXES = (".ttf", ".otf", ".ttc")
# sfnt 头：TrueType、CFF 版 OpenType、字体集合、老式 Apple TrueType。
# 用来挡掉「链接填成网页/CSS/JSON 却一路存成 .ttf」这类明显错误。
FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"ttcf", b"true")

GOOGLE_REPO = "google/fonts"
LXGW_REPO = "lxgw/LxgwWenKai"
# jsDelivr 的国内镜像：单文件上限 20 MB，超过会 403（Noto Serif SC、霞鹜文楷
# 都超了），所以它只是「第一顺位」而不是唯一来源，失败自动落到后面的源。
JSDELIVR_BASE = "https://cdn.jsdmirror.com/gh"
RAW_BASE = "https://raw.githubusercontent.com"
# GitHub 加速前缀：把 raw.githubusercontent.com 的整条地址拼在它后面。
# 实测国内直连 raw 只有几百字节每秒（25 MB 的文件等于下不完），这两个前缀
# 各有约 900 KB/s，所以大文件默认先走它们。用户可以配置自己的前缀覆盖。
MIRROR_PREFIXES = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
)

DOWNLOAD_TIMEOUT_SECONDS = 180
# 表目录通常只有几百字节，给 64 KB 足够容纳上百张表
TABLE_HEAD_BYTES = 64 * 1024
LICENSE_NAME = "OFL.txt"


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class FontFamily:
    """一个字体家族（= fonts/ 下的一个子目录）。"""

    key: str
    name: str
    weights: str
    repo: str
    files: tuple[str, ...]
    approx_mb: float
    note: str = ""


@dataclass(frozen=True)
class FontTemplate:
    """对接项目的一张报告模板 → 它主动使用的字体家族。

    ``key`` 是对接项目自己用的模板标识（也在它的配置里填这个名字），
    ``name_en`` 是照上游叫法给出的英文标题，``name`` 是给中文界面用的译名。
    """

    key: str
    name: str
    families: tuple[str, ...]
    note: str = ""
    name_en: str = ""


@dataclass
class FontJob:
    """后台下载任务的进度（同一时刻只跑一个）。"""

    kind: str
    label: str
    total: int = 0
    done: int = 0
    status: str = IDLE
    message: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    log: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.status == RUNNING

    def add_log(self, line: str) -> None:
        self.log.append(line)
        del self.log[:-40]

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "label": self.label,
            "total": self.total,
            "done": self.done,
            "status": self.status,
            "message": self.message,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
            "log": self.log[-8:],
        }


# ---------------------------------------------------------------- 字体清单

FONT_FAMILIES: dict[str, FontFamily] = {
    family.key: family
    for family in (
        FontFamily(
            "notosanssc",
            "Noto Sans SC",
            "400 / 500 / 600 / 700",
            GOOGLE_REPO,
            ("ofl/notosanssc/NotoSansSC[wght].ttf",),
            17.8,
            "中文黑体，绝大多数中文模板的正文",
        ),
        FontFamily(
            "notosanstc",
            "Noto Sans TC",
            "400 / 500 / 700 / 800",
            GOOGLE_REPO,
            ("ofl/notosanstc/NotoSansTC[wght].ttf",),
            12.0,
            "繁体黑体",
        ),
        FontFamily(
            "notosansjp",
            "Noto Sans JP",
            "400 / 700",
            GOOGLE_REPO,
            ("ofl/notosansjp/NotoSansJP[wght].ttf",),
            9.6,
            "日文黑体",
        ),
        FontFamily(
            "notoserifsc",
            "Noto Serif SC",
            "400 / 500 / 600 / 700 / 900",
            GOOGLE_REPO,
            ("ofl/notoserifsc/NotoSerifSC[wght].ttf",),
            25.1,
            "中文宋体，排版/复古类模板的标题",
        ),
        FontFamily(
            "notoseriftc",
            "Noto Serif TC",
            "400 / 500 / 600 / 700 / 900",
            GOOGLE_REPO,
            ("ofl/notoseriftc/NotoSerifTC[wght].ttf",),
            16.9,
            "繁体宋体",
        ),
        FontFamily(
            "inter",
            "Inter",
            "正体 + 斜体",
            GOOGLE_REPO,
            ("ofl/inter/Inter[opsz,wght].ttf", "ofl/inter/Inter-Italic[opsz,wght].ttf"),
            1.8,
            "西文正文/数字，英文报告常用",
        ),
        FontFamily(
            "jetbrainsmono",
            "JetBrains Mono",
            "正体 + 斜体",
            GOOGLE_REPO,
            (
                "ofl/jetbrainsmono/JetBrainsMono[wght].ttf",
                "ofl/jetbrainsmono/JetBrainsMono-Italic[wght].ttf",
            ),
            0.4,
            "等宽西文，代码风模板",
        ),
        FontFamily(
            "cinzel",
            "Cinzel",
            "600 / 700 / 800",
            GOOGLE_REPO,
            ("ofl/cinzel/Cinzel[wght].ttf",),
            0.1,
            "古典衬线大写标题",
        ),
        FontFamily(
            "cormorantgaramond",
            "Cormorant Garamond",
            "正体 + 斜体",
            GOOGLE_REPO,
            (
                "ofl/cormorantgaramond/CormorantGaramond[wght].ttf",
                "ofl/cormorantgaramond/CormorantGaramond-Italic[wght].ttf",
            ),
            1.9,
            "优雅衬线，艺术类模板",
        ),
        FontFamily(
            "outfit",
            "Outfit",
            "400 / 600 / 800",
            GOOGLE_REPO,
            ("ofl/outfit/Outfit[wght].ttf",),
            0.1,
            "几何无衬线，初音模板标题",
        ),
        FontFamily(
            "sharetechmono",
            "Share Tech Mono",
            "400",
            GOOGLE_REPO,
            ("ofl/sharetechmono/ShareTechMono-Regular.ttf",),
            0.05,
            "科技感等宽西文",
        ),
        FontFamily(
            "oswald",
            "Oswald",
            "400 / 600",
            GOOGLE_REPO,
            ("ofl/oswald/Oswald[wght].ttf",),
            0.2,
            "窄体西文，复古未来模板",
        ),
        FontFamily(
            "zcoolkuaile",
            "ZCOOL KuaiLe",
            "400",
            GOOGLE_REPO,
            ("ofl/zcoolkuaile/ZCOOLKuaiLe-Regular.ttf",),
            1.5,
            "站酷快乐体，手账类模板",
        ),
        FontFamily(
            "longcang",
            "Long Cang",
            "400",
            GOOGLE_REPO,
            ("ofl/longcang/LongCang-Regular.ttf",),
            5.2,
            "龙藏体，毛笔手写",
        ),
        FontFamily(
            "patrickhand",
            "Patrick Hand",
            "400",
            GOOGLE_REPO,
            ("ofl/patrickhand/PatrickHand-Regular.ttf",),
            0.2,
            "手写西文",
        ),
        FontFamily(
            "mashanzheng",
            "Ma Shan Zheng",
            "400",
            GOOGLE_REPO,
            ("ofl/mashanzheng/MaShanZheng-Regular.ttf",),
            5.9,
            "马善政毛笔楷书，春节模板",
        ),
        FontFamily(
            "zcoolxiaowei",
            "ZCOOL XiaoWei",
            "400",
            GOOGLE_REPO,
            ("ofl/zcoolxiaowei/ZCOOLXiaoWei-Regular.ttf",),
            6.3,
            "站酷小薇体，春节模板",
        ),
        FontFamily(
            "lxgwwenkai",
            "LXGW WenKai（霞鹜文楷）",
            "Regular / Medium / Mono Regular",
            LXGW_REPO,
            (
                "fonts/TTF/LXGWWenKai-Regular.ttf",
                "fonts/TTF/LXGWWenKai-Medium.ttf",
                "fonts/TTF/LXGWWenKaiMono-Regular.ttf",
            ),
            78.0,
            "ATRI 模板专用，三个文件一起装",
        ),
    )
}

FONT_TEMPLATES: dict[str, FontTemplate] = {
    template.key: template
    for template in (
        FontTemplate("simple", "简约", ("notosanssc", "notosanstc"), name_en="Simple"),
        FontTemplate("deepseek_maid", "DeepSeek 女仆", ("notosanssc",), name_en="DeepSeek Maid"),
        FontTemplate(
            "HatsuneMiku",
            "初音未来",
            ("notosanssc", "notosanstc", "notosansjp", "outfit", "sharetechmono", "inter"),
            "英文报告另外用到 Inter",
            name_en="Hatsune Miku",
        ),
        FontTemplate(
            "hack",
            "黑客终端",
            ("notosanssc", "notosanstc", "inter", "jetbrainsmono"),
            name_en="Hack",
        ),
        FontTemplate("format", "排版", ("notoserifsc", "notoseriftc", "inter"), name_en="Format"),
        FontTemplate(
            "maria",
            "Maria",
            ("notoserifsc", "notoseriftc", "cinzel", "cormorantgaramond", "jetbrainsmono"),
            name_en="Maria",
        ),
        FontTemplate(
            "art_nouveau",
            "新艺术",
            ("notoserifsc", "notoseriftc", "cinzel", "cormorantgaramond", "jetbrainsmono"),
            name_en="Art Nouveau",
        ),
        FontTemplate(
            "retro_futurism",
            "复古未来",
            ("notosanssc", "notosanstc", "oswald", "jetbrainsmono"),
            name_en="Retro Futurism",
        ),
        FontTemplate(
            "scrapbook",
            "剪贴簿",
            ("notosanssc", "notosanstc", "zcoolkuaile", "longcang", "patrickhand"),
            name_en="Scrapbook",
        ),
        FontTemplate(
            "spring_festival",
            "春节",
            ("notoserifsc", "notoseriftc", "mashanzheng", "zcoolxiaowei"),
            name_en="Spring Festival",
        ),
        FontTemplate(
            "ATRI",
            "ATRI",
            ("lxgwwenkai",),
            "需一次装好三个霞鹜文楷文件",
            name_en="ATRI",
        ),
    )
}

# 对接项目的字体清单文档：页面里直接给出，方便用户核对版本
UPSTREAM_DOC_URL = (
    "https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis"
    "/blob/main/docs/T2I_FONTS.md"
)
UPSTREAM_PROJECT = "astrbot_plugin_qq_group_daily_analysis（群聊日常分析）"


# ---------------------------------------------------------------- 工具函数


def human_mb(size_bytes: int) -> str:
    """字节数转成人读的大小；不到 1 MB 的显示 KB，免得小字体一律变成 0.0 MB。"""
    if size_bytes <= 0:
        return "0 KB"
    if size_bytes < 1024 * 1024:
        kb = size_bytes / 1024
        return f"{kb:.1f} KB" if kb < 10 else f"{kb:.0f} KB"
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def is_font_data(data: bytes) -> bool:
    """按 sfnt 魔数判断前若干字节是不是字体文件。"""
    return any(data.startswith(magic) for magic in FONT_MAGIC)


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _parse_name_table(raw: bytes) -> list[str]:
    """解析 sfnt 的 name 表，取出家族名（nameID 1 / 16）。

    排序上 nameID 16（印刷家族名）优先：可变字体把 nameID 1 写成
    「Noto Serif SC ExtraLight」这种「家族名 + 默认实例」，拿它当 CSS
    font-family 会匹配不到，只能用 16。
    """
    if len(raw) < 6:
        return []
    count = _u16(raw, 2)
    strings = _u16(raw, 4)
    typographic: list[str] = []
    legacy: list[str] = []
    names = legacy
    for index in range(count):
        record = 6 + index * 12
        if record + 12 > len(raw):
            break
        platform = _u16(raw, record)
        name_id = _u16(raw, record + 6)
        length = _u16(raw, record + 8)
        offset = _u16(raw, record + 10)
        if name_id not in (1, 16) or not length:
            continue
        chunk = raw[strings + offset : strings + offset + length]
        if not chunk:
            continue
        if platform in (0, 3):
            text = chunk.decode("utf-16-be", "ignore")
        elif platform == 1:
            text = chunk.decode("mac-roman", "ignore")
        else:
            continue
        text = text.replace("\x00", "").strip()
        if not text:
            continue
        bucket = typographic if name_id == 16 else legacy
        if text not in bucket:
            bucket.append(text)
    ordered = list(typographic)
    for name in legacy:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _names_from_sfnt(data: bytes, base: int) -> list[str]:
    if base + 12 > len(data):
        return []
    num_tables = _u16(data, base + 4)
    for index in range(num_tables):
        record = base + 12 + index * 16
        if record + 16 > len(data):
            break
        if data[record : record + 4] != b"name":
            continue
        offset = _u32(data, record + 8)
        length = _u32(data, record + 12)
        return _parse_name_table(data[offset : offset + length])
    return []


def _name_table_span(data: bytes, base: int) -> tuple[int, int] | None:
    """在表目录里找 name 表的（绝对偏移, 长度）。"""
    if base + 12 > len(data):
        return None
    num_tables = _u16(data, base + 4)
    for index in range(num_tables):
        record = base + 12 + index * 16
        if record + 16 > len(data):
            return None
        if data[record : record + 4] == b"name":
            return _u32(data, record + 8), _u32(data, record + 12)
    return None


def read_font_names_from_path(path: Path) -> list[str]:
    """按文件读家族名：只读表目录与 name 表，不把几十 MB 的字体读进内存。

    字体越大，name 表越可能落在文件末尾（Noto Serif SC 就是这样），所以
    不能靠「读前几 MB」蒙。
    """
    names: list[str] = []
    with path.open("rb") as handle:
        head = handle.read(TABLE_HEAD_BYTES)
        if len(head) < 12:
            return []
        chunks: list[bytes] = []
        if head[:4] == b"ttcf":
            count = min(_u32(head, 8), 8)
            for index in range(count):
                position = 12 + index * 4
                if position + 4 > len(head):
                    break
                handle.seek(_u32(head, position))
                chunks.append(handle.read(TABLE_HEAD_BYTES))
        else:
            chunks.append(head)
        for chunk in chunks:
            span = _name_table_span(chunk, 0)
            if span is None:
                continue
            offset, length = span
            if length <= 0 or length > 4 * 1024 * 1024:
                continue
            handle.seek(offset)
            for name in _parse_name_table(handle.read(length)):
                if name not in names:
                    names.append(name)
    return names


def read_font_names(data: bytes) -> list[str]:
    """读出字体的家族名。

    只用于给页面显示「这个文件到底叫什么」，读不到就退回文件名，因此这里
    不做严格校验：字体集合（ttc）取前几个子字体即可。
    """
    if not data:
        return []
    if data[:4] == b"ttcf" and len(data) >= 16:
        names: list[str] = []
        count = min(_u32(data, 8), 8)
        for index in range(count):
            offset_pos = 12 + index * 4
            if offset_pos + 4 > len(data):
                break
            for name in _names_from_sfnt(data, _u32(data, offset_pos)):
                if name not in names:
                    names.append(name)
        return names
    return _names_from_sfnt(data, 0)


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _is_font_name(name: str) -> bool:
    return name.lower().endswith(FONT_SUFFIXES)


def sanitize_filename(raw: str) -> str:
    """把外部来源的文件名压成一个安全的 basename。"""
    name = urllib.parse.unquote(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w\u4e00-\u9fff.()\[\] +\-]", "_", name, flags=re.UNICODE)
    name = name.strip(" .")
    if not name:
        return ""
    if not name.lower().endswith(FONT_SUFFIXES + (".txt",)):
        return ""
    # 文件名过长在部分文件系统上会直接报错
    if len(name) > 120:
        stem, dot, suffix = name.rpartition(".")
        name = stem[: 120 - len(suffix) - 1] + dot + suffix
    return name


def filename_from_url(url: str) -> str:
    """从下载链接里猜文件名；猜不出返回空串（由调用方兜底命名）。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return ""
    return sanitize_filename(parsed.path.rsplit("/", 1)[-1])


def validate_http_url(url: str) -> str:
    """校验自由输入的下载链接，返回去掉首尾空白的地址。"""
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("请填写字体下载链接")
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("只支持 http / https 链接")
    if not parsed.netloc:
        raise ValueError("链接缺少主机名")
    return cleaned


# ---------------------------------------------------------------- 管理器


class FontManager:
    """插件字体目录的读写与 fontconfig 供给。

    只依赖标准库（下载在子线程里用 urllib 完成），因此单测可以直接实例化，
    不需要 astrbot 运行环境。
    """

    def __init__(
        self,
        fonts_dir: Path,
        *,
        github_proxy: str = "",
        upload_limit_mb: int = 128,
        timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
        on_changed: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.fonts_dir = Path(fonts_dir)
        self.github_proxy = (github_proxy or "").strip()
        self.upload_limit_bytes = max(1, int(upload_limit_mb)) * 1024 * 1024
        self.timeout_seconds = max(30, int(timeout_seconds))
        self.on_changed = on_changed
        self.job: FontJob | None = None
        self._task: asyncio.Task | None = None
        self._name_cache: dict[str, tuple[float, int, list[str]]] = {}
        self._preferred_source: str = ""

    # ------------------------------------------------------------ 目录与配置

    @property
    def config_path(self) -> Path:
        return self.fonts_dir / "fontconfig.conf"

    def ensure_dir(self) -> None:
        self.fonts_dir.mkdir(parents=True, exist_ok=True)

    def render_fontconfig(self) -> str:
        """生成字体配置：先并入系统字体，再追加插件字体目录。

        必须 include 系统配置——设了 ``FONTCONFIG_FILE`` 后 fontconfig 只读这一
        个文件，不并入的话容器里的系统字体会全部消失，页面反而更丑。
        """
        cache_dir = self.fonts_dir / ".cache"
        return (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
            "<!-- 由 AstrBot 文转图插件自动生成，手动修改会在下次刷新时被覆盖 -->\n"
            "<fontconfig>\n"
            '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
            f"  <dir>{_xml_escape(str(self.fonts_dir))}</dir>\n"
            f"  <cachedir>{_xml_escape(str(cache_dir))}</cachedir>\n"
            "</fontconfig>\n"
        )

    def write_fontconfig(self) -> Path:
        """把字体配置写到字体目录下（原子写入）。"""
        self.ensure_dir()
        target = self.config_path
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(self.render_fontconfig(), encoding="utf-8")
        temp.replace(target)
        return target

    def apply_env(self) -> bool:
        """让本进程后续启动的 Chromium 读这份字体配置。

        Playwright 启动浏览器时继承进程环境变量，所以只能改 ``os.environ``；
        必须在 Chromium 启动前设置（与 ``PLAYWRIGHT_BROWSERS_PATH`` 同理）。
        """
        self.ensure_dir()
        if not self.config_path.exists():
            try:
                self.write_fontconfig()
            except OSError as exc:
                logger.warning(f"[t2i] 生成字体配置失败：{exc}")
                return False
        os.environ["FONTCONFIG_FILE"] = str(self.config_path)
        return True

    def env_active(self) -> bool:
        return os.environ.get("FONTCONFIG_FILE") == str(self.config_path)

    def refresh_cache(self) -> str:
        """刷新 fontconfig 缓存；系统里没有 fc-cache 时安静跳过。

        Chromium 内部有自己的字体枚举，不刷新也能看到新文件，但装了 fontconfig
        的工具（fc-list / fc-match）会读到旧缓存，导致用户按文档排查时对不上。
        """
        binary = shutil.which("fc-cache")
        if not binary:
            return "未找到 fc-cache，跳过缓存刷新"
        try:
            result = subprocess.run(
                [binary, "-f", str(self.fonts_dir)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"刷新字体缓存失败：{exc}"
        if result.returncode != 0:
            return f"刷新字体缓存失败：{(result.stderr or '').strip()[:200]}"
        return "字体缓存已刷新"

    # ------------------------------------------------------------ 已安装

    def _font_files(self) -> list[Path]:
        if not self.fonts_dir.is_dir():
            return []
        files: list[Path] = []
        for path in sorted(self.fonts_dir.rglob("*")):
            if not path.is_file() or not _is_font_name(path.name):
                continue
            if any(part.startswith(".") for part in path.relative_to(self.fonts_dir).parts):
                continue
            files.append(path)
        return files

    def _absolute_names(self, path: Path) -> list[str]:
        """读字体家族名，按 (mtime, size) 缓存，避免每次列表都重读 20 MB。"""
        try:
            stat = path.stat()
        except OSError:
            return []
        cached = self._name_cache.get(str(path))
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]
        names: list[str] = []
        try:
            names = read_font_names_from_path(path)[:6]
        except OSError as exc:
            logger.debug(f"[t2i] 读取字体名称失败 {path.name}：{exc}")
        if not names:
            names = [path.stem]
        self._name_cache[str(path)] = (stat.st_mtime, stat.st_size, names)
        return names

    def list_installed(self) -> list[dict[str, object]]:
        """列出字体目录里的文件（含家族名、大小、所属子目录）。"""
        items: list[dict[str, object]] = []
        for path in self._font_files():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            relative = path.relative_to(self.fonts_dir)
            items.append(
                {
                    "path": relative.as_posix(),
                    "group": relative.parts[0] if len(relative.parts) > 1 else "",
                    "filename": path.name,
                    "size": size,
                    "size_text": human_mb(size),
                    "families": self._absolute_names(path),
                }
            )
        return items

    def installed_by_family(self) -> dict[str, list[str]]:
        """按目录名归组已安装字体，用于判断某个家族是否下载过。"""
        groups: dict[str, list[str]] = {}
        for item in self.list_installed():
            group = str(item["group"])
            groups.setdefault(group, []).append(str(item["filename"]))
        return groups

    def family_installed(self, key: str, installed: dict[str, list[str]] | None = None) -> bool:
        """判断某个家族的必需文件是否都在。"""
        family = FONT_FAMILIES.get(key)
        if family is None:
            return False
        have = (installed if installed is not None else self.installed_by_family()).get(key, [])
        needed = [Path(item).name for item in family.files]
        return all(name in have for name in needed)

    def total_bytes(self) -> int:
        total = 0
        for path in self._font_files():
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    # ------------------------------------------------------------ 目录数据

    def catalog(self) -> dict[str, object]:
        """给页面用的清单：字体家族 + 模板分组 + 已安装状态。"""
        installed = self.installed_by_family()
        families = [
            {
                "key": family.key,
                "name": family.name,
                "weights": family.weights,
                "approx_mb": family.approx_mb,
                "note": family.note,
                "files": [Path(item).name for item in family.files],
                "repo": family.repo,
                "installed": self.family_installed(family.key, installed),
            }
            for family in FONT_FAMILIES.values()
        ]
        templates = []
        for template in FONT_TEMPLATES.values():
            missing = [
                key for key in template.families if not self.family_installed(key, installed)
            ]
            size = sum(FONT_FAMILIES[key].approx_mb for key in template.families)
            templates.append(
                {
                    "key": template.key,
                    "name": template.name,
                    "name_en": template.name_en or template.key,
                    "note": template.note,
                    "families": list(template.families),
                    "family_names": [FONT_FAMILIES[key].name for key in template.families],
                    "approx_mb": round(size, 1),
                    "installed": not missing,
                    "missing": missing,
                }
            )
        return {
            "families": families,
            "templates": templates,
            "upstream": {"project": UPSTREAM_PROJECT, "doc": UPSTREAM_DOC_URL},
        }

    def status(self) -> dict[str, object]:
        installed = self.list_installed()
        groups = self.installed_by_family()
        return {
            "dir": str(self.fonts_dir),
            "config_path": str(self.config_path),
            "config_exists": self.config_path.exists(),
            "env_active": self.env_active(),
            "env_value": os.environ.get("FONTCONFIG_FILE", ""),
            "files": len(installed),
            "families": sum(1 for key in FONT_FAMILIES if self.family_installed(key, groups)),
            "total_bytes": self.total_bytes(),
            "total_text": human_mb(self.total_bytes()),
            "fc_cache": shutil.which("fc-cache") is not None,
            "upload_limit_mb": self.upload_limit_bytes // (1024 * 1024),
            "job": self.job.as_dict() if self.job else None,
        }

    # ------------------------------------------------------------ 下载

    def _source_urls(self, family: FontFamily, relative: str) -> list[str]:
        """按优先级给出同一个文件的多个下载源。

        jsDelivr 镜像在国内快，但单文件超过 20 MB 会 403（Noto Serif SC、
        霞鹜文楷都属于这种情况），所以大文件把加速前缀排前面、小文件把
        jsDelivr 排前面；GitHub 直连永远垫底。
        """
        raw = f"{RAW_BASE}/{family.repo}/main/{relative}"
        cdn = f"{JSDELIVR_BASE}/{family.repo}@main/{relative}"
        mirrors = [f"{prefix}{raw}" for prefix in MIRROR_PREFIXES]
        if family.approx_mb >= 18:
            sources = mirrors + [cdn, raw]
        else:
            sources = [cdn] + mirrors + [raw]
        if self.github_proxy:
            sources.insert(0, f"{self.github_proxy.rstrip('/')}/{raw}")
        # 上次成功的来源排到最前：同一批文件通常来自同一个可用的源，
        # 省掉一次必然失败的往返
        if self._preferred_source in sources:
            sources.remove(self._preferred_source)
            sources.insert(0, self._preferred_source)
        return sources

    def _fetch(self, urls: list[str], target: Path, *, expect_font: bool) -> str:
        """依次尝试多个来源，成功返回实际使用的地址。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        errors: list[str] = []
        for url in urls:
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "astrbot-plugin-t2i-service"}
                )
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    head = response.read(4096)
                    if expect_font and not is_font_data(head):
                        raise ValueError("返回内容不是字体文件")
                    with partial.open("wb") as handle:
                        handle.write(head)
                        shutil.copyfileobj(response, handle)
                partial.replace(target)
                return url
            except (OSError, ValueError, urllib.error.URLError) as exc:
                errors.append(f"{urllib.parse.urlparse(url).netloc}：{exc}")
                logger.debug(f"[t2i] 字体下载源失败 {url}：{exc}")
                partial.unlink(missing_ok=True)
        raise ValueError("；".join(errors) or "没有可用的下载源")

    async def _download_family(self, job: FontJob, key: str) -> None:
        family = FONT_FAMILIES[key]
        for relative in family.files:
            target = self.fonts_dir / key / Path(relative).name
            job.message = f"下载 {family.name} / {target.name}"
            job.add_log(f"↓ {family.name}：{target.name}")
            source = await asyncio.to_thread(
                self._fetch,
                self._source_urls(family, relative),
                target,
                expect_font=True,
            )
            self._preferred_source = source
            job.done += 1
        # 许可文件跟着字体走：分发给别人时要能说明来源
        license_relative = (
            f"{Path(family.files[0]).parent}/{LICENSE_NAME}"
            if family.repo == GOOGLE_REPO
            else LICENSE_NAME
        )
        license_target = self.fonts_dir / key / LICENSE_NAME
        if not license_target.exists():
            try:
                await asyncio.to_thread(
                    self._fetch,
                    self._source_urls(family, license_relative),
                    license_target,
                    expect_font=False,
                )
            except ValueError as exc:
                job.add_log(f"授权文件 {LICENSE_NAME} 未能下载：{exc}")

    async def _download_url(self, job: FontJob, url: str, filename: str) -> None:
        target = self.fonts_dir / "custom" / filename
        job.add_log(f"↓ {url}")
        await asyncio.to_thread(self._fetch, [url], target, expect_font=True)
        job.done += 1

    async def _run_job(
        self, job: FontJob, work: Awaitable[None]
    ) -> None:
        job.status = RUNNING
        try:
            await work
            job.status = DONE
            job.message = job.message or "完成"
        except asyncio.CancelledError:
            job.status = FAILED
            job.message = "任务已取消"
            raise
        except Exception as exc:  # noqa: BLE001 - 后台任务必须自己兜住异常
            job.status = FAILED
            job.message = str(exc)
            logger.warning(f"[t2i] 字体下载失败：{exc}")
        finally:
            job.finished = time.time()
            await self.after_change()
            if job.status == DONE:
                logger.info(f"[t2i] 字体任务完成：{job.label}")

    async def after_change(self) -> None:
        """字体变动后统一收尾：重建配置、刷新缓存、按需重启浏览器。"""
        try:
            await asyncio.to_thread(self.write_fontconfig)
        except OSError as exc:
            logger.warning(f"[t2i] 重写字体配置失败：{exc}")
        try:
            detail = await asyncio.to_thread(self.refresh_cache)
            logger.debug(f"[t2i] {detail}")
        except Exception as exc:  # noqa: BLE001 - 缓存刷新失败不影响字体本身
            logger.debug(f"[t2i] 刷新字体缓存异常：{exc}")
        if self.on_changed is not None:
            try:
                await self.on_changed()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[t2i] 字体变更后的回调失败：{exc}")

    def _spawn(self, job: FontJob, work: Awaitable[None]) -> FontJob:
        if self.job is not None and self.job.running:
            raise RuntimeError("已有字体任务在进行中，请等待完成")
        self.job = job
        self._task = asyncio.create_task(self._run_job(job, work))
        return job

    def start_families(self, keys: list[str]) -> FontJob:
        """下载若干字体家族（模板一键下载也走这里）。"""
        wanted: list[str] = []
        for key in keys:
            if key not in FONT_FAMILIES:
                raise ValueError(f"未知字体：{key}")
            if key not in wanted:
                wanted.append(key)
        if not wanted:
            raise ValueError("请选择要下载的字体")
        total = sum(len(FONT_FAMILIES[key].files) for key in wanted)
        label = "、".join(FONT_FAMILIES[key].name for key in wanted)
        job = FontJob(kind="family", label=label, total=total, status=IDLE)

        async def work() -> None:
            for key in wanted:
                await self._download_family(job, key)

        return self._spawn(job, work())

    def start_template(self, template_key: str) -> FontJob:
        """一键下载某个对接模板需要的整套字体。"""
        template = FONT_TEMPLATES.get(template_key)
        if template is None:
            raise ValueError(f"未知模板：{template_key}")
        job = self.start_families(list(template.families))
        job.kind = "template"
        job.label = f"模板 {template.name_en or template.key}：" + job.label
        return job

    def start_url(self, url: str, filename: str = "") -> FontJob:
        """自由链接下载：只存到 fonts/custom/ 下，不改动家族目录。"""
        cleaned = validate_http_url(url)
        name = sanitize_filename(filename) or filename_from_url(cleaned)
        if not name or not _is_font_name(name):
            raise ValueError("无法从链接判断字体文件名，请手动填写以 .ttf / .otf / .ttc 结尾的文件名")
        job = FontJob(kind="url", label=name, total=1, status=IDLE)
        return self._spawn(job, self._download_url(job, cleaned, name))

    async def install_upload(self, filename: str, data: bytes) -> dict[str, object]:
        """保存上传的字体文件，返回落盘信息。"""
        name = sanitize_filename(filename)
        if not name or not _is_font_name(name):
            raise ValueError("只接受 .ttf / .otf / .ttc 字体文件")
        if not data:
            raise ValueError("上传内容为空")
        if len(data) > self.upload_limit_bytes:
            limit = self.upload_limit_bytes // (1024 * 1024)
            raise ValueError(f"文件超过 {limit} MB 上限")
        if not is_font_data(data[:4096]):
            raise ValueError("这不是有效的字体文件（请确认不是改名的网页或压缩包）")
        target = self.fonts_dir / "uploaded" / name
        if target.exists():
            raise ValueError(f"{name} 已存在，请先删除旧文件")
        await asyncio.to_thread(self._write_bytes, target, data)
        await self.after_change()
        return {
            "path": target.relative_to(self.fonts_dir).as_posix(),
            "size": len(data),
            "size_text": human_mb(len(data)),
            "families": await asyncio.to_thread(self._absolute_names, target),
        }

    def _write_bytes(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".part")
        temp.write_bytes(data)
        temp.replace(target)

    # ------------------------------------------------------------ 删除

    def remove(self, relative: str) -> str:
        """删除字体目录下的一个文件；只允许删字体与授权文本。"""
        cleaned = (relative or "").strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("缺少文件路径")
        root = self.fonts_dir.resolve()
        target = (self.fonts_dir / cleaned).resolve()
        if not target.is_relative_to(root) or target == root:
            raise ValueError("非法的文件路径")
        if target.name == self.config_path.name:
            raise ValueError("字体配置文件不能删除")
        if not target.is_file():
            raise ValueError("文件不存在")
        if not (_is_font_name(target.name) or target.name.lower().endswith(".txt")):
            raise ValueError("只能删除字体文件或授权文件")
        target.unlink()
        self._name_cache.pop(str(target), None)
        # 家族目录空了就顺手清掉，页面上的分组不会留空壳
        parent = target.parent
        try:
            if parent != root and not any(parent.iterdir()):
                parent.rmdir()
        except OSError as exc:
            logger.debug(f"[t2i] 清理空字体目录失败：{exc}")
        logger.info(f"[t2i] 已删除字体文件：{cleaned}")
        return cleaned

    def cancel(self) -> None:
        """插件卸载时取消正在跑的下载任务。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
