"""字体供给：清单一致性、文件名清洗、fontconfig 生成、上传/删除与页面接口注册。"""

import struct

import pytest

from t2i_plugin import font_web
from t2i_plugin.font_web import FontWebApi
from t2i_plugin.fonts import (
    FONT_FAMILIES,
    FONT_TEMPLATES,
    FontManager,
    filename_from_url,
    human_mb,
    is_font_data,
    read_font_names,
    read_font_names_from_path,
    sanitize_filename,
    validate_http_url,
)


def _build_sfnt(family: str, base: int = 0) -> bytes:
    """拼一个最小 sfnt：只有 name 表，够 read_font_names 读出家族名。

    base 用于字体集合：子字体的表偏移相对整个 .ttc 文件，不是相对子字体起点。
    """
    entries = [(1, family), (16, family)]
    records = b""
    storage = b""
    for name_id, text in entries:
        chunk = text.encode("utf-16-be")
        records += struct.pack(
            ">HHHHHH", 3, 1, 0x409, name_id, len(chunk), len(storage)
        )
        storage += chunk
    name_table = struct.pack(">HHH", 0, len(entries), 6 + 12 * len(entries))
    name_table += records + storage
    header = b"\x00\x01\x00\x00" + struct.pack(">HHHH", 1, 16, 0, 0)
    record = b"name" + b"\x00" * 4 + struct.pack(">II", base + 28, len(name_table))
    return header + record + name_table


# ---------------------------------------------------------------- 字体解析


def test_read_font_names_from_ttf():
    data = _build_sfnt("Noto Sans SC")
    assert is_font_data(data) is True
    assert read_font_names(data) == ["Noto Sans SC"]


def test_typographic_family_name_wins():
    """可变字体把 nameID 1 写成「家族名 + 默认实例」，家族名要取 nameID 16。"""
    entries = [(1, "Noto Serif SC ExtraLight"), (16, "Noto Serif SC")]
    records = b""
    storage = b""
    for name_id, text in entries:
        chunk = text.encode("utf-16-be")
        records += struct.pack(">HHHHHH", 3, 1, 0x409, name_id, len(chunk), len(storage))
        storage += chunk
    name_table = struct.pack(">HHH", 0, len(entries), 6 + 12 * len(entries))
    name_table += records + storage
    header = b"\x00\x01\x00\x00" + struct.pack(">HHHH", 1, 16, 0, 0)
    record = b"name" + b"\x00" * 4 + struct.pack(">II", 28, len(name_table))
    data = header + record + name_table
    assert read_font_names(data) == ["Noto Serif SC", "Noto Serif SC ExtraLight"]


def test_read_font_names_from_path_seeks_name_table(tmp_path):
    """name 表在文件末尾的大字体也要读得到（不能只读开头几 MB）。"""
    font = tmp_path / "big.ttf"
    body = _build_sfnt("Late Table")
    font.write_bytes(body + b"\x00" * (256 * 1024))
    assert read_font_names_from_path(font) == ["Late Table"]


def test_read_font_names_from_collection():
    ttc_header = b"ttcf" + struct.pack(">HHI", 1, 0, 1) + struct.pack(">I", 16)
    ttc = ttc_header + _build_sfnt("LXGW WenKai", base=len(ttc_header))
    assert is_font_data(ttc) is True
    assert read_font_names(ttc) == ["LXGW WenKai"]


def test_read_font_names_ignores_garbage():
    assert read_font_names(b"") == []
    assert read_font_names(b"<html>not a font</html>") == []
    assert is_font_data(b"<html>") is False


# ---------------------------------------------------------------- 文件名与链接


def test_human_mb_keeps_small_sizes_readable():
    assert human_mb(0) == "0 KB"
    assert human_mb(42 * 1024) == "42 KB"
    assert human_mb(2 * 1024) == "2.0 KB"
    assert human_mb(24 * 1024 * 1024) == "24.0 MB"


def test_sanitize_filename_strips_paths_and_rejects_other_suffixes():
    assert sanitize_filename("../../etc/passwd") == ""
    assert sanitize_filename("https://x/y/My%20Font.ttf") == "My Font.ttf"
    assert sanitize_filename("font.TTF") == "font.TTF"
    assert sanitize_filename("readme.md") == ""
    assert sanitize_filename("") == ""


def test_filename_from_url():
    assert filename_from_url("https://a.test/x/NotoSansSC.ttf") == "NotoSansSC.ttf"
    assert filename_from_url("https://a.test/x/?file=a.ttf") == ""


@pytest.mark.parametrize("url", ["", "   ", "file:///tmp/a.ttf", "ftp://a/b.ttf", "https://"])
def test_validate_http_url_rejects(url: str):
    with pytest.raises(ValueError):
        validate_http_url(url)


def test_validate_http_url_accepts_http_and_trims():
    assert validate_http_url("  https://a.test/a.ttf ") == "https://a.test/a.ttf"
    assert validate_http_url("http://a.test/a.otf") == "http://a.test/a.otf"


# ---------------------------------------------------------------- 清单


def test_catalog_matches_known_templates():
    manager = FontManager(_tmp(None), github_proxy="")
    catalog = manager.catalog()
    assert len(catalog["families"]) == len(FONT_FAMILIES)
    assert len(catalog["templates"]) == len(FONT_TEMPLATES)
    for template in catalog["templates"]:
        # 模板引用的家族必须都在清单里，否则页面上的补齐按钮会指到不存在的项
        assert set(template["families"]) <= set(FONT_FAMILIES)
        assert template["missing"] == list(template["families"])
        assert template["installed"] is False
    upstream = catalog["upstream"]
    assert "qq_group_daily_analysis" in upstream["project"]
    assert upstream["doc"].endswith("docs/T2I_FONTS.md")


def test_unknown_family_and_template_are_rejected(tmp_path):
    manager = FontManager(tmp_path / "fonts")
    with pytest.raises(ValueError):
        manager.start_families(["not-a-font"])
    with pytest.raises(ValueError):
        manager.start_template("no-such-template")


# ---------------------------------------------------------------- fontconfig


def test_write_fontconfig_lists_font_files(tmp_path):
    manager = FontManager(tmp_path / "fonts")
    manager.ensure_dir()
    (manager.fonts_dir / "custom").mkdir()
    (manager.fonts_dir / "custom" / "A.ttf").write_bytes(_build_sfnt("A Family"))
    (manager.fonts_dir / "custom" / "notes.txt").write_text("nope", encoding="utf-8")

    text = manager.render_fontconfig()
    assert "<dir>" in text and "A.ttf" not in text
    assert str(manager.fonts_dir) in text
    assert "<fontconfig>" in text

    path = manager.write_fontconfig()
    assert path == manager.config_path
    assert path.exists()


def test_apply_env_points_fontconfig_at_plugin_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    manager = FontManager(tmp_path / "fonts")
    assert manager.env_active() is False
    assert manager.apply_env() is True
    assert manager.env_active() is True
    assert manager.config_path.exists()


# ---------------------------------------------------------------- 上传与删除


async def test_install_upload_writes_file_and_reports_family(tmp_path):
    manager = FontManager(tmp_path / "fonts")
    info = await manager.install_upload("My Font.ttf", _build_sfnt("My Font"))
    assert info["path"] == "uploaded/My Font.ttf"
    assert info["families"] == ["My Font"]
    assert manager.family_installed("custom-family") is False
    assert [item["filename"] for item in manager.list_installed()] == ["My Font.ttf"]


async def test_install_upload_rejects_bad_payloads(tmp_path):
    manager = FontManager(tmp_path / "fonts", upload_limit_mb=1)
    with pytest.raises(ValueError):
        await manager.install_upload("a.zip", _build_sfnt("X"))
    with pytest.raises(ValueError):
        await manager.install_upload("a.ttf", b"")
    with pytest.raises(ValueError):
        await manager.install_upload("a.ttf", b"<html>not a font</html>")
    with pytest.raises(ValueError):
        await manager.install_upload("a.ttf", b"\x00\x01\x00\x00" + b"x" * (2 << 20))


async def test_install_upload_refuses_overwrite(tmp_path):
    manager = FontManager(tmp_path / "fonts")
    await manager.install_upload("a.ttf", _build_sfnt("A"))
    with pytest.raises(ValueError):
        await manager.install_upload("a.ttf", _build_sfnt("A"))


async def test_remove_guards_paths_and_cleans_empty_group(tmp_path):
    manager = FontManager(tmp_path / "fonts")
    await manager.install_upload("gone.ttf", _build_sfnt("Gone"))
    with pytest.raises(ValueError):
        manager.remove("../outside.ttf")
    with pytest.raises(ValueError):
        manager.remove("uploaded/missing.ttf")
    with pytest.raises(ValueError):
        manager.remove("fontconfig.conf")

    removed = manager.remove("uploaded/gone.ttf")
    assert removed == "uploaded/gone.ttf"
    assert manager.list_installed() == []
    # 家族目录空了要一起清掉，页面上不留空壳分组
    assert not (manager.fonts_dir / "uploaded").exists()


# ---------------------------------------------------------------- 页面接口


class _FakeContext:
    def __init__(self) -> None:
        self.routes: list[tuple[str, object, list[str], str]] = []

    def register_web_api(self, route, handler, methods, desc) -> None:
        self.routes.append((route, handler, methods, desc))


def test_font_web_registers_prefixed_routes(tmp_path):
    api = FontWebApi("astrbot_plugin_t2i_service", FontManager(tmp_path / "fonts"))
    context = _FakeContext()
    routes = api.register(context)
    assert len(routes) == len(context.routes) == 8
    for route, handler, methods, desc in context.routes:
        assert route.startswith("/astrbot_plugin_t2i_service/fonts/")
        assert methods in (["GET"], ["POST"])
        assert desc
        assert callable(handler)


def test_font_web_skips_registration_without_web_api(tmp_path):
    api = FontWebApi("astrbot_plugin_t2i_service", FontManager(tmp_path / "fonts"))
    assert api.register(object()) == []
    assert api.registered == []


def test_font_web_collects_installed_family_names(tmp_path):
    manager = FontManager(tmp_path / "fonts")
    (manager.fonts_dir / "a").mkdir(parents=True)
    (manager.fonts_dir / "a" / "A.ttf").write_bytes(_build_sfnt("Alpha"))
    (manager.fonts_dir / "b").mkdir(parents=True)
    (manager.fonts_dir / "b" / "B.ttf").write_bytes(_build_sfnt("Beta"))
    names = font_web._installed_family_names(manager)
    assert names == ["Alpha", "Beta"]


def _tmp(unused):
    """catalog() 只需要一个临时目录，测试里用不到它的返回值。"""
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp()) / "fonts"
