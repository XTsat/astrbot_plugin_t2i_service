"""内嵌 HTTP 服务的契约测试。

重点不是「能跑」，而是**与 AstrBot 主程序真正对接的那条路径**：
``POST {endpoint}/generate`` 返回 ``{"code":0,"data":{"id":"data/x.png"}}``，
主程序再按 ``{endpoint}/{id}`` 取图。因此除了直接打接口，这里还用 AstrBot
自带的客户端（``astrbot.core.utils.t2i.network_strategy.NetworkRenderStrategy``）
真跑一遍，防止我们「自测通过、上线 404」。
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from t2i_core.render import ScreenshotOptions
from t2i_plugin import server as t2i_server
from t2i_plugin.config import T2IConfig
from t2i_plugin.server import T2IHttpServer, normalize_render_url


class FakeRenderer:
    """记录调用参数的假渲染器，避免测试依赖 Chromium。"""

    def __init__(self, tmp_path: Path, suffix: str = ".png") -> None:
        self.tmp_path = tmp_path
        self.suffix = suffix
        self.calls: list[tuple[str, object]] = []

    async def from_html(self, html: str) -> tuple[str, str]:
        path = self.tmp_path / "render.html"
        path.write_text(html, encoding="utf-8")
        self.calls.append(("from_html", html))
        return html, str(path)

    async def from_jinja_template(self, template: str, data: dict) -> tuple[str, str]:
        path = self.tmp_path / "render.html"
        path.write_text(template, encoding="utf-8")
        self.calls.append(("from_jinja_template", (template, data)))
        return template, str(path)

    async def html2pic(self, html_path: str, options: ScreenshotOptions) -> str:
        self.calls.append(("html2pic", options))
        suffix = ".png" if options.type == "png" else ".jpg"
        picture = self.tmp_path / f"out{suffix}"
        picture.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
        return str(picture)


class FakeBrowserManager:
    def __init__(self, installed: bool = True) -> None:
        self.installed = installed

    def is_installed(self) -> bool:
        return self.installed

    def status(self) -> dict:
        return {"installed": self.installed}


@pytest.fixture
def config() -> T2IConfig:
    config = T2IConfig.from_raw({})
    config.server.fallback_enabled = False
    return config


@pytest.fixture
async def client(tmp_path: Path, config: T2IConfig):
    renderer = FakeRenderer(tmp_path)
    server = T2IHttpServer(
        config=config,
        renderer=renderer,
        data_dir=str(tmp_path),
        browser_manager=FakeBrowserManager(),
    )
    test_client = TestClient(TestServer(server._build_app()))
    await test_client.start_server()
    try:
        yield test_client, server, renderer
    finally:
        await test_client.close()


# --------------------------------------------------------------------- 工具


def test_normalize_render_url_matches_astrbot_client():
    assert normalize_render_url("") == ""
    assert normalize_render_url("http://a:1/") == "http://a:1/text2img"
    assert normalize_render_url("http://a:1/text2img") == "http://a:1/text2img"
    assert normalize_render_url("  http://a:1  ") == "http://a:1/text2img"


# ----------------------------------------------------------- 生成接口契约


async def test_generate_json_returns_data_id_then_image_is_downloadable(client):
    test_client, _, renderer = client
    body = {
        "tmpl": "<h1>{{ title }}</h1>",
        "json": True,
        "tmpldata": {"title": "hi"},
        "options": {"type": "png", "full_page": True},
    }
    resp = await test_client.post("/text2img/generate", json=body)
    assert resp.status == 200
    payload = await resp.json()
    assert payload["code"] == 0
    image_id = payload["data"]["id"]
    assert image_id.startswith("data/")

    # 主程序用的正是这个拼接方式：{endpoint}/{id}
    image = await test_client.get(f"/text2img/{image_id}")
    assert image.status == 200
    assert image.headers["Content-Type"] == "image/png"
    assert await image.read() == b"\x89PNG\r\n\x1a\nfake-image-bytes"

    options = [call for name, call in renderer.calls if name == "html2pic"][0]
    assert options.type == "png"
    assert options.full_page is True


async def test_generate_binary_mode_returns_image_bytes(client):
    test_client, _, _ = client
    resp = await test_client.post(
        "/text2img/generate",
        json={"html": "<p>hello</p>", "options": {"type": "jpeg", "quality": 40}},
    )
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "image/jpeg"
    assert (await resp.read()).startswith(b"\x89PNG")  # 假渲染器固定输出的字节


async def test_generate_without_html_or_tmpl_is_400(client):
    test_client, _, _ = client
    resp = await test_client.post("/text2img/generate", json={"json": True})
    assert resp.status == 400
    payload = await resp.json()
    assert payload["code"] == 1
    assert payload["message"] == "html or tmpl not found"
    assert payload["data"] == {}


async def test_tmplname_is_rejected_with_clear_message(client):
    test_client, _, _ = client
    resp = await test_client.post(
        "/text2img/generate", json={"tmplname": "base", "json": True}
    )
    assert resp.status == 400
    assert "tmplname" in (await resp.json())["message"]


async def test_invalid_json_body_is_400(client):
    test_client, _, _ = client
    resp = await test_client.post(
        "/text2img/generate", data=b"not-json", headers={"Content-Type": "application/json"}
    )
    assert resp.status == 400


async def test_invalid_options_are_400(client):
    test_client, _, _ = client
    resp = await test_client.post(
        "/text2img/generate",
        json={"html": "<p>x</p>", "options": {"type": "webp"}},
    )
    assert resp.status == 400
    assert "invalid options" in (await resp.json())["message"]


async def test_escaped_exception_is_logged_and_keeps_response_shape(
    client, monkeypatch
):
    """漏网异常照旧是 aiohttp 的 500 纯文本，但服务端必须留下一条带堆栈的日志。

    事故现场：渲染失败只是个起点，真正让日志全空的是记日志那行自己抛了异常。
    """
    test_client, server, _ = client
    records: list[tuple[str, str]] = []

    class RecordingLogger:
        def debug(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("debug", message))

        def info(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("info", message))

        def warning(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("warning", message))

        def error(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("error", message))

        def exception(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("exception", message))

    async def boom(payload: dict):
        raise RuntimeError("磁盘写满")

    monkeypatch.setattr(t2i_server, "logger", RecordingLogger())
    monkeypatch.setattr(server, "_handle_payload", boom)
    resp = await test_client.post("/text2img/generate", json={"html": "<p>x</p>"})

    assert resp.status == 500
    levels = [level for level, _ in records]
    assert "exception" in levels
    assert any("磁盘写满" in message for _, message in records)
    # 访问日志仍然照写，失败也能对上时间线
    assert any("POST /text2img/generate" in message for _, message in records)


async def test_every_request_leaves_an_access_log_line(client, monkeypatch):
    """aiohttp 的 access_log 写进 stdlib logging，插件日志页看不到，必须自己写一条。"""
    test_client, _, _ = client
    records: list[tuple[str, str]] = []

    class RecordingLogger:
        def debug(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("debug", message))

        def info(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("info", message))

        def warning(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("warning", message))

        def error(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("error", message))

        def exception(self, message: str, *args: object, **kwargs: object) -> None:
            records.append(("exception", message))

    monkeypatch.setattr(t2i_server, "logger", RecordingLogger())
    resp = await test_client.post("/text2img/generate", json={"html": "<p>x</p>"})
    assert resp.status == 200

    lines = [message for _, message in records]
    assert any("POST /text2img/generate" in line and "200" in line for line in lines)


async def test_security_error_from_template_is_400(client, monkeypatch):
    test_client, _, renderer = client
    from jinja2.exceptions import SecurityError

    async def boom(template: str, data: dict):
        raise SecurityError("access to attribute 'x' is unsafe")

    monkeypatch.setattr(renderer, "from_jinja_template", boom)
    resp = await test_client.post("/text2img/generate", json={"tmpl": "{{ x }}"})
    assert resp.status == 400
    assert (await resp.json())["message"].startswith("security error")


async def test_data_route_404_for_unknown_id(client):
    test_client, _, _ = client
    resp = await test_client.get("/text2img/data/nope.png")
    assert resp.status == 404
    assert (await resp.json())["code"] == 1


async def test_health_endpoint(client):
    test_client, server, _ = client
    resp = await test_client.get("/healthz")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["status"] == "ok"
    assert payload["requests"] == server.requests_served


# --------------------------------------------------------------------- 鉴权


async def test_auth_token_is_enforced(tmp_path: Path, config: T2IConfig):
    config.server.auth_token = "s3cret"
    renderer = FakeRenderer(tmp_path)
    server = T2IHttpServer(
        config=config, renderer=renderer, data_dir=str(tmp_path)
    )
    test_client = TestClient(TestServer(server._build_app()))
    await test_client.start_server()
    try:
        resp = await test_client.post(
            "/text2img/generate", json={"html": "<p>x</p>"}
        )
        assert resp.status == 401

        resp = await test_client.post(
            "/text2img/generate",
            json={"html": "<p>x</p>"},
            headers={"Authorization": "Bearer s3cret"},
        )
        assert resp.status == 200

        # /healthz 供自检脚本使用，不做鉴权
        assert (await test_client.get("/healthz")).status == 200
    finally:
        await test_client.close()


# --------------------------------------------------------------- 备用端点转发


async def test_local_render_unavailable_and_fallback_disabled_is_503(
    tmp_path: Path, config: T2IConfig
):
    renderer = FakeRenderer(tmp_path)
    server = T2IHttpServer(
        config=config,
        renderer=renderer,
        data_dir=str(tmp_path),
        browser_manager=FakeBrowserManager(installed=False),
    )
    test_client = TestClient(TestServer(server._build_app()))
    await test_client.start_server()
    try:
        resp = await test_client.post("/text2img/generate", json={"html": "<p>x</p>"})
        assert resp.status == 503
        assert (await resp.json())["code"] == 1
        assert not [c for c in renderer.calls if c[0] == "from_html"]
    finally:
        await test_client.close()


PNG_BYTES = b"\x89PNG\r\n\x1a\nfallback-image-bytes"


async def _start_upstream(handler) -> tuple[TestServer, list]:
    """起一个假的备用端点，返回 (server, 请求记录)。"""
    seen: list[web.Request] = []

    async def generate(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return await handler(request)

    app = web.Application()
    app.router.add_post("/text2img/generate", generate)
    app.router.add_get("/text2img/data/{image_id}", handler_data)
    server = TestServer(app)
    await server.start_server()
    return server, seen


async def handler_data(request: web.Request) -> web.Response:
    return web.Response(body=PNG_BYTES, content_type="image/png")


async def _start_plugin(tmp_path: Path, upstream_port: int, installed: bool = False):
    config = T2IConfig.from_raw({})
    config.server.fallback_endpoint = f"http://127.0.0.1:{upstream_port}/text2img"
    server = T2IHttpServer(
        config=config,
        renderer=FakeRenderer(tmp_path),
        data_dir=str(tmp_path),
        browser_manager=FakeBrowserManager(installed=installed),
    )
    test_client = TestClient(TestServer(server._build_app()))
    await test_client.start_server()
    return test_client, server


async def test_fallback_saves_upstream_image_and_returns_local_id(tmp_path: Path):
    """本机不可用时透传；把上游图片落到本地，id 指向本插件的 /text2img/data。"""

    async def upstream_generate(request: web.Request) -> web.Response:
        return web.Response(body=PNG_BYTES, content_type="image/png")

    upstream, seen = await _start_upstream(upstream_generate)
    try:
        test_client, server = await _start_plugin(tmp_path, upstream.port)
        try:
            resp = await test_client.post(
                "/text2img/generate", json={"html": "<p>x</p>", "json": True}
            )
            assert resp.status == 200
            image_id = (await resp.json())["data"]["id"]
            assert image_id.startswith("data/fallback_")
            # 上游永远被要求直接回图片字节，避免拿到对岸的临时 id
            assert seen == [{"html": "<p>x</p>", "json": False}]
            assert server.fallback_used == 1

            # 主程序随后按 /text2img/data/{id} 取图，必须真能取到
            image = await test_client.get(f"/text2img/{image_id}")
            assert image.status == 200
            assert await image.read() == PNG_BYTES
        finally:
            await test_client.close()
            await server.stop()
    finally:
        await upstream.close()


async def test_fallback_without_json_returns_image_bytes(tmp_path: Path):
    """调用方要字节流时直接回图片，但同样要落盘（便于之后按 id 复取）。"""

    async def upstream_generate(request: web.Request) -> web.Response:
        return web.Response(body=PNG_BYTES, content_type="image/png")

    upstream, _ = await _start_upstream(upstream_generate)
    try:
        test_client, server = await _start_plugin(tmp_path, upstream.port)
        try:
            resp = await test_client.post("/text2img/generate", json={"html": "<p>x</p>"})
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "image/png"
            assert await resp.read() == PNG_BYTES
            assert server.fallback_used == 1
        finally:
            await test_client.close()
            await server.stop()
    finally:
        await upstream.close()


async def test_fallback_follows_upstream_id_when_upstream_returns_json(tmp_path: Path):
    """上游忽略 json=false、仍回 {"data":{"id":...}} 时，按 id 再取一次图片。"""

    async def upstream_generate(request: web.Request) -> web.Response:
        return web.json_response(
            {"code": 0, "message": "success", "data": {"id": "data/upstream.png"}}
        )

    upstream, _ = await _start_upstream(upstream_generate)
    try:
        test_client, server = await _start_plugin(tmp_path, upstream.port)
        try:
            resp = await test_client.post(
                "/text2img/generate", json={"html": "<p>x</p>", "json": True}
            )
            assert resp.status == 200
            image_id = (await resp.json())["data"]["id"]
            assert image_id.startswith("data/fallback_")
            image = await test_client.get(f"/text2img/{image_id}")
            assert await image.read() == PNG_BYTES
        finally:
            await test_client.close()
            await server.stop()
    finally:
        await upstream.close()


async def test_fallback_upstream_error_is_503_with_message(tmp_path: Path):
    """备用端点自己报错时，把它的原因回给调用方，而不是回一张坏图。"""

    async def upstream_generate(request: web.Request) -> web.Response:
        return web.json_response(
            {"code": 1, "message": "render queue wait exceeded", "data": {}},
            status=429,
        )

    upstream, _ = await _start_upstream(upstream_generate)
    try:
        test_client, server = await _start_plugin(tmp_path, upstream.port)
        try:
            resp = await test_client.post(
                "/text2img/generate", json={"html": "<p>x</p>", "json": True}
            )
            assert resp.status == 503
            payload = await resp.json()
            assert payload["code"] == 1
            assert "render queue wait exceeded" in payload["message"]
            assert server.fallback_used == 0
        finally:
            await test_client.close()
            await server.stop()
    finally:
        await upstream.close()


# ---------------------------------------------------- 与真实 AstrBot 客户端对接


async def test_real_astrbot_client_can_render_through_us(client):
    """用 AstrBot 自带的远端策略真跑一次：改契约时这个测试会第一时间炸。"""
    test_client, _, _ = client
    network_strategy = pytest.importorskip(
        "astrbot.core.utils.t2i.network_strategy",
        reason="需要 AstrBot 运行环境",
    )
    strategy = network_strategy.NetworkRenderStrategy(
        base_url=f"http://127.0.0.1:{test_client.port}/text2img"
    )
    url = await strategy.render_custom_template(
        "<h1>{{ title }}</h1>", {"title": "end2end"}, return_url=True
    )
    # AstrBot 客户端默认 options = {full_page: True, type: "jpeg", quality: 40}
    assert url.endswith("/text2img/data/out.jpg")

    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            assert resp.status == 200
            assert await resp.read() == b"\x89PNG\r\n\x1a\nfake-image-bytes"
    # 模板与数据确实被透传给渲染器。注意 AstrBot 客户端会先把 shiki 运行时
    # 内联进模板（{% raw %} 包裹），所以这里断言包含关系而不是全等。
    templates = [call for name, call in client[2].calls if name == "from_jinja_template"]
    assert templates, "渲染器没有被调用"
    template, data = templates[0]
    assert "<h1>{{ title }}</h1>" in template
    assert data == {"title": "end2end"}


def test_json_payload_roundtrip_shape():
    """把主程序的 post_data 形状固化成常量，便于对照上游文档。"""
    post_data = {
        "tmpl": "<h1>{{ title }}</h1>",
        "json": True,
        "tmpldata": {"title": "x"},
        "options": {"full_page": True, "type": "jpeg", "quality": 40},
    }
    assert json.loads(json.dumps(post_data)) == post_data


# -------------------------------------------------- 插件重载后的端口接管


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_on(tmp_path: Path, port: int) -> T2IHttpServer:
    config = T2IConfig.from_raw(
        {"server": {"host": "127.0.0.1", "port": port, "fallback_enabled": False}}
    )
    return T2IHttpServer(
        config=config,
        renderer=FakeRenderer(tmp_path),
        data_dir=str(tmp_path),
        browser_manager=FakeBrowserManager(),
    )


async def test_new_instance_takes_over_port_held_by_previous_load(tmp_path: Path):
    """AstrBot 连续加载两次插件时，新实例要停掉旧监听再绑定，而不是报端口占用。"""
    port = _free_port()
    previous = _server_on(tmp_path, port)
    ok, detail = await previous.start()
    assert ok, detail

    current = _server_on(tmp_path, port)
    ok, detail = await current.start()
    try:
        assert ok, detail
        assert current.running
        assert not previous.running
    finally:
        await current.stop()


async def test_stop_unregisters_so_port_can_be_reused(tmp_path: Path):
    port = _free_port()
    server = _server_on(tmp_path, port)
    assert await server.start()
    await server.stop()

    again = _server_on(tmp_path, port)
    ok, detail = await again.start()
    try:
        assert ok, detail
    finally:
        await again.stop()


async def test_port_held_by_foreign_program_reports_actionable_error(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(t2i_server, "PORT_BIND_RETRY_SECONDS", 0.2)
    port = _free_port()
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        server = _server_on(tmp_path, port)
        ok, detail = await server.start()
        assert ok is False
        assert "其它程序" in detail
        assert not server.running
    finally:
        blocker.close()


async def test_port_held_by_previous_instance_is_reported_as_own_service(
    tmp_path: Path, monkeypatch
):
    """旧版本加载的实例占着端口又没登记时无法接管，要明确告出「是本插件自己」。"""
    monkeypatch.setattr(t2i_server, "PORT_BIND_RETRY_SECONDS", 0.3)
    port = _free_port()
    zombie = _server_on(tmp_path, port)
    assert await zombie.start()
    t2i_server._server_registry().clear()
    try:
        server = _server_on(tmp_path, port)
        ok, detail = await server.start()
        assert ok is False
        assert "由上一次加载的插件实例占用" in detail
    finally:
        await zombie.stop()
