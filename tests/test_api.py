import asyncio
import json
from pathlib import Path

import pytest


# 这些是独立服务（FastAPI/存储后端）的测试：缺少对应依赖时整体跳过，
# 插件模式（AstrBot 环境）不需要它们。
pytest.importorskip("fastapi")
pytest.importorskip("prometheus_client")
from t2i_core import api
from t2i_core.storage import StoredImage


class FakeRenderer:
    def __init__(self, output_path: Path):
        self.output_path = output_path

    async def from_html(self, html: str):
        return "data/rendered_test.html", str(self.output_path.with_suffix(".html"))

    async def html2pic(self, html_file_path, screenshot_options):
        self.output_path.write_bytes(b"png")
        return str(self.output_path)


class FakeStorage:
    def __init__(self):
        self.saved_path: str | None = None

    async def save(self, image_path: str) -> str:
        self.saved_path = image_path
        return "rendered_test.png"

    async def get(self, image_id: str):
        if image_id == "rendered_test.png":
            return StoredImage(content=b"png", media_type="image/png")
        return None


def test_generate_json_response_remains_compatible(tmp_path, monkeypatch):
    output_path = tmp_path / "rendered_test.png"
    storage = FakeStorage()
    monkeypatch.setattr(api, "render", FakeRenderer(output_path))
    monkeypatch.setattr(api, "image_storage", storage)

    response = asyncio.run(
        api.text2img(
            api.GenerateRequest(
                html="<html><body>test</body></html>",
                json=True,
            )
        )
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "code": 0,
        "message": "success",
        "data": {"id": "data/rendered_test.png"},
    }
    assert storage.saved_path == str(output_path)


def test_get_image_response_remains_compatible(monkeypatch):
    monkeypatch.setattr(api, "image_storage", FakeStorage())

    response = asyncio.run(api.text2img_image("rendered_test.png"))

    assert response.status_code == 200
    assert response.body == b"png"
    assert response.media_type == "image/png"


def test_get_missing_image_response_remains_compatible(monkeypatch):
    monkeypatch.setattr(api, "image_storage", FakeStorage())

    response = asyncio.run(api.text2img_image("missing.png"))

    assert response.status_code == 404
    assert json.loads(response.body) == {
        "code": 1,
        "message": "file not found",
        "data": {},
    }
