"""文转图模板：清单合并、来源判定、槽位写入与页面接口。

模板目录走的是 AstrBot 的路径助手，测试里把两个目录换成临时目录，
用文件内容模拟「内置模板」与「用户自建模板」。
"""

import json

import pytest

from t2i_plugin import template_web, templates
from t2i_plugin.template_web import TemplateWebApi
from t2i_plugin.templates import PREVIEW_KEEP, SLOT_NAME, TemplateGallery

# 带 title / description / Shiki 运行时的模板，覆盖 describe() 的解析分支
RICH = """<!doctype html>
<html>
  <head>
    <title>{{ version }} 群聊日报</title>
    <meta name="description" content="表格与代码块都好看的日报模板" />
  </head>
  <body>
    <textarea id="markdown-source">{{ text | safe }}</textarea>
    <script>{{ shiki_runtime | safe }}</script>
  </body>
</html>
"""

# 没有 title/description 的最简模板
PLAIN = """<html><body><textarea>{{ text }}</textarea></body></html>"""


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """两个模板目录 + 一个 TemplateGallery；返回 (gallery, builtin_dir, user_dir)。"""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    (builtin / "base.html").write_text(RICH, encoding="utf-8")
    (builtin / "astrbot_powershell.html").write_text(PLAIN, encoding="utf-8")
    # 用户目录里的同名副本改了内容，AstrBot 会拿它覆盖内置模板
    (user / "astrbot_powershell.html").write_text(
        PLAIN.replace("{{ text }}", "{{ text }} 改过的"), encoding="utf-8"
    )
    (user / "自建模板.html").write_text(PLAIN, encoding="utf-8")
    monkeypatch.setattr(templates, "builtin_template_dir", lambda: builtin)
    monkeypatch.setattr(templates, "user_template_dir", lambda: user)
    gallery = TemplateGallery(tmp_path / "data", plugin_version="v1.2.1")
    return gallery, builtin, user


# ---------------------------------------------------------------- 清单与解析


def test_list_templates_merges_both_dirs(dirs):
    gallery, _, _ = dirs
    names = {item["name"] for item in gallery.list_templates()}
    assert names == {"base", "astrbot_powershell", "自建模板"}


def test_list_templates_hides_slot_and_marks_applied(dirs):
    gallery, _, user = dirs
    (user / f"{SLOT_NAME}.html").write_text(RICH, encoding="utf-8")
    items = gallery.list_templates()
    assert SLOT_NAME not in {item["name"] for item in items}
    # 槽位内容与 base 完全一致，等于「当前用的是 base」
    assert {item["name"]: item["applied"] for item in items}["base"] is True


def test_describe_reads_title_description_and_variables(dirs):
    gallery, _, _ = dirs
    item = gallery.describe("base")
    # 标题里的 {{ version }} 对用户没有意义，展示时去掉
    assert item["title"] == "群聊日报"
    assert item["description"] == "表格与代码块都好看的日报模板"
    assert item["variables"] == ["shiki_runtime", "text", "version"]
    assert item["shiki"] is True
    assert item["source"] == "builtin"
    assert item["modified"] is False
    assert item["bytes"] > 0


def test_describe_falls_back_to_name_without_title(dirs):
    gallery, _, _ = dirs
    item = gallery.describe("自建模板")
    assert item["title"] == "自建模板"
    assert item["description"] == ""
    assert item["source"] == "custom"
    assert item["shiki"] is False


def test_describe_marks_modified_user_copy(dirs):
    gallery, _, _ = dirs
    item = gallery.describe("astrbot_powershell")
    assert item["source"] == "builtin"
    assert item["modified"] is True


def test_read_prefers_user_dir_and_rejects_bad_names(dirs):
    gallery, _, _ = dirs
    assert "改过的" in gallery.read("astrbot_powershell")
    for bad in ("", "../etc/passwd", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            gallery.read(bad)
    with pytest.raises(FileNotFoundError):
        gallery.read("不存在的模板")


def test_sample_data_matches_astrbot_render_variables(dirs):
    gallery, _, _ = dirs
    data = gallery.sample_data()
    assert set(data) == {"text", "version"}
    assert data["version"] == "v1.2.1"
    assert len(data["text"]) > 40


# ---------------------------------------------------------------- 槽位写入


def test_apply_writes_slot_and_tracks_source(dirs):
    gallery, _, _ = dirs
    result = gallery.apply("base")
    assert result["slot"] == SLOT_NAME
    assert result["source"] == "base"
    assert result["changed"] is True
    assert gallery.slot_path.read_text(encoding="utf-8") == RICH
    assert gallery.applied_source() == "base"

    # 同一套再应用一次：内容没变，不算改动
    assert gallery.apply("base")["changed"] is False


def test_apply_leaves_no_temp_file(dirs):
    gallery, _, _ = dirs
    gallery.apply("base")
    assert sorted(path.name for path in gallery.user_dir.glob("*")) == [
        "astrbot_powershell.html",
        f"{SLOT_NAME}.html",
        "自建模板.html",
    ]


def test_applied_source_empty_when_slot_hand_edited(dirs):
    gallery, _, user = dirs
    (user / f"{SLOT_NAME}.html").write_text("<html>手改的内容</html>", encoding="utf-8")
    assert gallery.applied_source() == ""


def test_applied_source_empty_without_slot(dirs):
    gallery, _, _ = dirs
    assert gallery.applied_source() == ""


# ---------------------------------------------------------------- 预览缓存


def test_preview_cache_follows_content_hash(dirs, tmp_path):
    gallery, _, _ = dirs
    rendered = tmp_path / "rendered.jpg"
    rendered.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    assert gallery.cached_preview("base") is None

    stored = gallery.store_preview("base", rendered)
    assert stored.is_file()
    assert gallery.cached_preview("base") == stored

    # 模板内容一改，哈希变了，旧缓存自然不再命中
    (gallery.user_dir / "base.html").write_text(RICH + "\n<!-- 改过 -->", encoding="utf-8")
    assert gallery.cached_preview("base") is None


def test_preview_cache_prunes_old_files(dirs, tmp_path):
    gallery, _, user = dirs
    rendered = tmp_path / "rendered.jpg"
    rendered.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    for index in range(PREVIEW_KEEP + 3):
        (user / f"t{index}.html").write_text(PLAIN + str(index), encoding="utf-8")
        gallery.store_preview(f"t{index}", rendered)
    assert len(list(gallery.preview_dir.glob("*.jpg"))) == PREVIEW_KEEP


# ---------------------------------------------------------------- 页面接口


# ---------------------------------------------------------------- 新建与编辑


def test_create_writes_blank_scaffold(dirs):
    gallery, _, user = dirs
    item = gallery.create("my_style")
    saved = (user / "my_style.html").read_text(encoding="utf-8")
    # 空白模板要能直接出图：带上 AstrBot 注入的正文变量
    assert "{{ text | safe }}" in saved
    assert item["name"] == "my_style"
    assert item["source"] == "custom"
    assert "my_style" in {entry["name"] for entry in gallery.list_templates()}


def test_create_copies_existing_template(dirs):
    gallery, _, user = dirs
    gallery.create("copy_of_base", source="base")
    assert (user / "copy_of_base.html").read_text(encoding="utf-8") == RICH


def test_create_prefers_pasted_content_over_copy(dirs):
    gallery, _, user = dirs
    gallery.create("pasted", source="base", content=PLAIN + "\n<!-- pasted -->")
    saved = (user / "pasted.html").read_text(encoding="utf-8")
    assert "pasted" in saved
    assert saved != RICH


def test_create_rejects_bad_and_taken_names(dirs):
    gallery, _, _ = dirs
    for bad in ("../evil", "has space", "", SLOT_NAME, "-leading"):
        with pytest.raises(ValueError):
            gallery.create(bad)
    with pytest.raises(FileExistsError):
        gallery.create("base")  # 内置同名也算已存在
    gallery.create("dup")
    with pytest.raises(FileExistsError):
        gallery.create("dup")


def test_save_overwrites_user_copy_and_marks_modified(dirs):
    gallery, _, user = dirs
    gallery.save("base", "<html>{{ text }} saved</html>")
    assert "saved" in (user / "base.html").read_text(encoding="utf-8")
    # 改过内置模板后，清单里要能看出这一份是本地改过的
    assert gallery.describe("base")["modified"] is True
    with pytest.raises(ValueError):
        gallery.save("base", "   ")


def test_create_and_save_store_display_name(dirs):
    gallery, _, user = dirs
    item = gallery.create("named", title=" 我的日报  ")
    assert item["title"] == "我的日报"
    assert item["custom_title"] is True
    # 显示名和模板文件分开存：文件名（AstrBot 配置里的标识）不变
    assert (user / "named.html").is_file()
    assert json.loads((user / "_titles.json").read_text(encoding="utf-8")) == {
        "named": "我的日报"
    }
    # 编辑时可以改名
    assert gallery.save("named", PLAIN, title="我的日报 v2")["title"] == "我的日报 v2"
    # 清空显示名后回落到模板自带的 <title>（这里 PLAIN 没有 title，用文件名兜底）
    assert gallery.save("named", PLAIN, title="")["title"] == "named"
    assert gallery.describe("named")["custom_title"] is False
    assert not (user / "_titles.json").exists()


def test_display_name_wins_over_template_title(dirs):
    gallery, _, _ = dirs
    assert gallery.describe("base")["custom_title"] is False
    gallery.save("base", RICH, title="日报卡片")
    item = gallery.describe("base")
    assert (item["title"], item["custom_title"]) == ("日报卡片", True)


def test_display_name_rejects_overlong_title(dirs):
    gallery, _, _ = dirs
    with pytest.raises(ValueError):
        gallery.create("toolong", title="名" * 65)


def test_delete_removes_user_template(dirs):
    gallery, _, user = dirs
    gallery.create("throwaway")
    result = gallery.delete("throwaway")
    assert result == {"name": "throwaway", "deleted": True, "shadowed": False}
    assert not (user / "throwaway.html").exists()
    assert "throwaway" not in {item["name"] for item in gallery.list_templates()}


def test_delete_user_copy_restores_builtin(dirs):
    gallery, _, user = dirs
    assert gallery.describe("astrbot_powershell")["modified"] is True
    result = gallery.delete("astrbot_powershell")
    # 删掉用户副本后内置那一版又露出来，页面据此提示「恢复到自带的那一版」
    assert result["shadowed"] is True
    assert gallery.describe("astrbot_powershell")["modified"] is False


def test_delete_refuses_builtin_missing_and_slot(dirs):
    gallery, _, user = dirs
    with pytest.raises(ValueError):
        gallery.delete("base")  # 只在内置目录里，删不了
    with pytest.raises(FileNotFoundError):
        gallery.delete("nope")
    with pytest.raises(ValueError):
        gallery.delete(SLOT_NAME)
    with pytest.raises(ValueError):
        gallery.delete("../etc/passwd")


def test_delete_drops_preview_cache(dirs, tmp_path):
    gallery, _, _ = dirs
    gallery.create("cached")
    rendered = tmp_path / "shot.jpg"
    rendered.write_bytes(b"jpeg")
    assert gallery.store_preview("cached", rendered).is_file()
    gallery.delete("cached")
    assert gallery.cached_preview("cached") is None


def test_save_leaves_no_temp_file(dirs):
    gallery, _, user = dirs
    gallery.create("tmpcheck")
    gallery.save("tmpcheck", PLAIN)
    assert not list(user.glob("*.tmp"))


class _FakeRequest:
    def __init__(self, payload=None) -> None:
        self.payload = payload or {}

    async def json(self, default=None):
        return self.payload or (default or {})


class _FakeContext:
    def __init__(self) -> None:
        self.routes: list[tuple[str, object, list[str], str]] = []

    def register_web_api(self, route, handler, methods, desc) -> None:
        self.routes.append((route, handler, methods, desc))


def _patch_envelope(monkeypatch) -> None:
    """把桥的响应包成可断言的字典：这里验的是接口逻辑，不是 AstrBot 的封装。"""
    monkeypatch.setattr(
        template_web, "json_response", lambda payload: {"code": 0, "data": payload}
    )
    monkeypatch.setattr(
        template_web,
        "error_response",
        lambda message, status_code=400: {"code": status_code, "message": message},
    )


def _build_api(
    gallery, *, preview=None, draft=None, applied=None, state=None
) -> TemplateWebApi:
    async def default_preview(name: str) -> tuple[bool, str]:
        return False, "预览不可用"

    async def default_draft(content: str) -> tuple[bool, str]:
        return False, "预览不可用"

    async def default_apply(name: str) -> dict[str, object]:
        return gallery.apply(name)

    async def default_state() -> dict[str, object]:
        return {"name": SLOT_NAME, "active": False, "source": ""}

    return TemplateWebApi(
        "astrbot_plugin_t2i_service",
        gallery,
        preview=preview or default_preview,
        preview_draft=draft or default_draft,
        apply=applied or default_apply,
        state=state or default_state,
    )


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有插件页面 Web API"
)
def test_web_registers_prefixed_template_routes(dirs):
    gallery, _, _ = dirs
    context = _FakeContext()
    routes = _build_api(gallery).register(context)
    assert len(routes) == len(context.routes) == 8
    for route, handler, methods, desc in context.routes:
        assert route.startswith("/astrbot_plugin_t2i_service/templates/")
        assert methods in (["GET"], ["POST"])
        assert desc
        assert callable(handler)


def test_web_skips_registration_without_web_api(dirs):
    gallery, _, _ = dirs
    api = _build_api(gallery)
    assert api.register(object()) == []
    assert api.registered == []


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有插件页面 Web API"
)
async def test_web_list_returns_templates_and_slot(dirs, monkeypatch):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    api = _build_api(
        gallery,
        state=lambda: _slot_state(),
    )
    response = await api.page_list()
    assert response["code"] == 0
    payload = response["data"]
    assert payload["page_api"] is True
    assert {item["name"] for item in payload["templates"]} == {
        "base",
        "astrbot_powershell",
        "自建模板",
    }
    assert payload["slot"]["name"] == SLOT_NAME


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有插件页面 Web API"
)
async def test_web_preview_renders_then_serves_cache(dirs, monkeypatch, tmp_path):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    rendered = tmp_path / "rendered.jpg"
    rendered.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    calls: list[str] = []

    async def preview(name: str) -> tuple[bool, str]:
        calls.append(name)
        return True, str(rendered)

    api = _build_api(gallery, preview=preview)
    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "base"}))

    first = await api.page_preview()
    assert first["code"] == 0
    assert first["data"]["image"].startswith("data:image/jpeg;base64,")
    assert first["data"]["cached"] is False

    second = await api.page_preview()
    assert second["data"]["cached"] is True
    # 第二次走缓存，没有再渲染一遍
    assert calls == ["base"]


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有插件页面 Web API"
)
async def test_web_preview_reports_render_failure(dirs, monkeypatch):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    api = _build_api(gallery, preview=lambda name: _failed_preview())
    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "base"}))
    response = await api.page_preview()
    assert response["code"] == 500
    assert "Chromium" in response["message"]


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有插件页面 Web API"
)
async def test_web_draft_preview_renders_unsaved_content(dirs, monkeypatch, tmp_path):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    rendered = tmp_path / "draft.jpg"
    rendered.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    seen: list[str] = []

    async def draft(content: str) -> tuple[bool, str]:
        seen.append(content)
        return True, str(rendered)

    api = _build_api(gallery, draft=draft)
    monkeypatch.setattr(
        template_web, "request", _FakeRequest({"content": "<h1>{{ text }}</h1>"})
    )
    response = await api.page_preview_draft()
    assert response["code"] == 0
    assert response["data"]["image"].startswith("data:image/jpeg;base64,")
    assert seen == ["<h1>{{ text }}</h1>"]

    # 草稿没有模板名可以挂缓存：第二次照样现渲，也不在预览目录里留图
    await api.page_preview_draft()
    assert len(seen) == 2
    assert list(gallery.preview_dir.glob("*.jpg")) == []


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有插件页面 Web API"
)
async def test_web_draft_preview_rejects_empty_content(dirs, monkeypatch):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    api = _build_api(gallery)
    monkeypatch.setattr(template_web, "request", _FakeRequest({"content": "   \n"}))
    response = await api.page_preview_draft()
    assert response["code"] == 400
    assert "不能为空" in response["message"]


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有插件页面 Web API"
)
async def test_web_apply_writes_slot_and_reports_restart(dirs, monkeypatch):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)

    async def applied(name: str) -> dict[str, object]:
        return {
            **gallery.apply(name),
            "wrote": ["t2i_active_template"],
            "restart_required": True,
        }

    api = _build_api(gallery, applied=applied)
    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "base"}))
    response = await api.page_apply()
    assert response["code"] == 0
    assert response["data"]["slot"] == SLOT_NAME
    assert response["data"]["restart_required"] is True
    assert gallery.slot_content() == RICH


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有插件页面 Web API"
)
async def test_web_apply_rejects_bad_names(dirs, monkeypatch):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    api = _build_api(gallery)

    monkeypatch.setattr(template_web, "request", _FakeRequest({}))
    assert (await api.page_apply())["code"] == 400

    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "../x"}))
    assert (await api.page_apply())["code"] == 400

    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "不存在"}))
    response = await api.page_apply()
    assert response["code"] == 400
    assert "不存在" in response["message"]


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有页面 Web API"
)
async def test_web_create_then_read(dirs, monkeypatch):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    api = _build_api(gallery)

    monkeypatch.setattr(
        template_web, "request", _FakeRequest({"name": "made", "source": "base"})
    )
    response = await api.page_create()
    assert response["code"] == 0
    assert response["data"]["created"] is True
    assert response["data"]["template"]["name"] == "made"

    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "made"}))
    read = await api.page_read()
    assert read["code"] == 0
    assert read["data"]["content"] == RICH

    # 重名要能区分出来：页面据此提示「换个名字」而不是「保存失败」
    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "made"}))
    assert (await api.page_create())["code"] == 409


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有页面 Web API"
)
async def test_web_save_and_read_errors(dirs, monkeypatch):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    api = _build_api(gallery)

    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "nope"}))
    assert (await api.page_read())["code"] == 404

    monkeypatch.setattr(template_web, "request", _FakeRequest({}))
    assert (await api.page_create())["code"] == 400
    assert (await api.page_save())["code"] == 400
    assert (await api.page_read())["code"] == 400

    monkeypatch.setattr(
        template_web,
        "request",
        _FakeRequest({"name": "made", "content": "<html>{{ text }}</html>"}),
    )
    saved = await api.page_save()
    assert saved["code"] == 0
    assert saved["data"]["created"] is False
    assert gallery.describe("made")["source"] == "custom"


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有页面 Web API"
)
async def test_web_create_and_save_pass_display_title(dirs, monkeypatch):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    api = _build_api(gallery)

    monkeypatch.setattr(
        template_web,
        "request",
        _FakeRequest({"name": "titled", "content": PLAIN, "title": "我的日报"}),
    )
    created = await api.page_create()
    assert created["code"] == 0
    assert created["data"]["template"]["title"] == "我的日报"
    assert created["data"]["template"]["custom_title"] is True

    monkeypatch.setattr(
        template_web,
        "request",
        _FakeRequest({"name": "titled", "content": PLAIN, "title": "改名了"}),
    )
    saved = await api.page_save()
    assert saved["code"] == 0
    assert saved["data"]["template"]["title"] == "改名了"


@pytest.mark.skipif(
    not template_web.WEB_API_AVAILABLE, reason="当前 AstrBot 没有页面 Web API"
)
async def test_web_delete(dirs, monkeypatch):
    gallery, _, _ = dirs
    _patch_envelope(monkeypatch)
    api = _build_api(gallery)
    gallery.create("gone")

    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "gone"}))
    response = await api.page_delete()
    assert response["code"] == 0
    assert response["data"]["deleted"] is True
    assert "gone" not in {item["name"] for item in gallery.list_templates()}

    # 内置模板与不存在的模板：页面靠状态码区分「删不了」和「已经没了」
    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "base"}))
    assert (await api.page_delete())["code"] == 400
    monkeypatch.setattr(template_web, "request", _FakeRequest({"name": "nope"}))
    assert (await api.page_delete())["code"] == 404
    monkeypatch.setattr(template_web, "request", _FakeRequest({}))
    assert (await api.page_delete())["code"] == 400


async def _slot_state() -> dict[str, object]:
    return {"name": SLOT_NAME, "active": True, "source": "base", "current": SLOT_NAME}


async def _failed_preview() -> tuple[bool, str]:
    return False, "Chromium 没起来"
