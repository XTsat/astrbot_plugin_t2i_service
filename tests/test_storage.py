import asyncio
import io
from pathlib import Path

import pytest

# 这些是独立服务（FastAPI/存储后端）的测试：缺少对应依赖时整体跳过，
# 插件模式（AstrBot 环境）不需要它们。
pytest.importorskip("botocore")
from botocore.exceptions import ClientError

from t2i_core import storage as storage_module
from t2i_core.storage import LocalImageStorage, S3ImageStorage, create_image_storage


class FakeS3Client:
    def __init__(self):
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.objects[(bucket, key)] = (
            Path(filename).read_bytes(),
            ExtraArgs["ContentType"],
        )

    def get_object(self, *, Bucket, Key):
        try:
            content, content_type = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "not found"}},
                "GetObject",
            ) from exc
        return {"Body": io.BytesIO(content), "ContentType": content_type}


def create_s3_storage(client):
    return S3ImageStorage(
        bucket="test-bucket",
        endpoint_url="https://example.invalid",
        access_key_id="access-key",
        secret_access_key="secret-key",
        prefix="images",
        client=client,
    )


def test_local_storage_round_trip_and_media_type(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    image_path = data_dir / "rendered_test.png"
    image_path.write_bytes(b"png")
    storage = LocalImageStorage(str(data_dir))

    image_id = asyncio.run(storage.save(str(image_path)))
    stored = asyncio.run(storage.get(image_id))

    assert image_id == "rendered_test.png"
    assert stored is not None
    assert stored.path == image_path
    assert stored.media_type == "image/png"


def test_local_storage_rejects_unsafe_id(tmp_path):
    storage = LocalImageStorage(str(tmp_path))

    assert asyncio.run(storage.get("../secret")) is None
    assert asyncio.run(storage.get("nested/image.png")) is None


def test_s3_storage_round_trip_and_removes_local_file(tmp_path):
    image_path = tmp_path / "rendered_test.png"
    image_path.write_bytes(b"png")
    client = FakeS3Client()
    storage = create_s3_storage(client)

    image_id = asyncio.run(storage.save(str(image_path)))
    stored = asyncio.run(storage.get(image_id))

    assert image_id == "rendered_test.png"
    assert not image_path.exists()
    assert stored is not None
    assert stored.content == b"png"
    assert stored.media_type == "image/png"
    assert ("test-bucket", "images/rendered_test.png") in client.objects


def test_s3_storage_keeps_local_file_when_upload_fails(tmp_path):
    class FailingS3Client(FakeS3Client):
        def upload_file(self, filename, bucket, key, ExtraArgs):
            raise RuntimeError("upload failed")

    image_path = tmp_path / "rendered_test.png"
    image_path.write_bytes(b"png")
    storage = create_s3_storage(FailingS3Client())

    with pytest.raises(RuntimeError, match="upload failed"):
        asyncio.run(storage.save(str(image_path)))

    assert image_path.exists()


def test_s3_storage_returns_none_for_missing_object():
    storage = create_s3_storage(FakeS3Client())

    assert asyncio.run(storage.get("missing.png")) is None


def test_standard_s3_uses_boto3_default_resolution(monkeypatch):
    captured: dict = {}
    fake_client = FakeS3Client()

    def fake_boto3_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        return fake_client

    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    for name in (
        "S3_ENDPOINT_URL",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(storage_module.boto3, "client", fake_boto3_client)

    storage = create_image_storage()

    assert isinstance(storage, S3ImageStorage)
    assert captured["service_name"] == "s3"
    assert set(captured["kwargs"]) == {"config"}


def test_r2_requires_endpoint(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "r2")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)

    with pytest.raises(
        ValueError, match="S3_ENDPOINT_URL is required when STORAGE_BACKEND=r2"
    ):
        create_image_storage()
