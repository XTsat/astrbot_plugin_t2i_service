import asyncio
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

try:  # boto3 仅 S3/R2 后端需要，插件模式默认用本地存储
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - 取决于运行环境
    boto3 = None  # type: ignore[assignment]
    Config = None  # type: ignore[assignment]

    class ClientError(Exception):  # type: ignore[no-redef]
        """boto3 缺失时的占位异常。"""

    BOTO3_AVAILABLE = False

# Keep MIME resolution deterministic in minimal container images.
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/webp", ".webp")


@dataclass(frozen=True)
class StoredImage:
    media_type: str
    content: bytes | None = None
    path: Path | None = None


class ImageStorage(Protocol):
    async def save(self, image_path: str) -> str:
        """Persist an image and return the ID accepted by get."""
        ...

    async def get(self, image_id: str) -> StoredImage | None:
        """Return an image by ID, or None when it does not exist."""
        ...


def _safe_filename(image_id: str) -> str | None:
    normalized = image_id.replace("\\", "/")
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        return None
    return normalized


def _media_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


class LocalImageStorage:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)

    async def save(self, image_path: str) -> str:
        return Path(image_path).name

    async def get(self, image_id: str) -> StoredImage | None:
        filename = _safe_filename(image_id)
        if filename is None:
            return None

        path = self.data_dir / filename
        if not path.is_file():
            return None

        return StoredImage(path=path, media_type=_media_type(filename))


class S3ImageStorage:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        region: str | None = None,
        prefix: str = "",
        client=None,
    ):
        if not bucket:
            raise ValueError("S3_BUCKET is required for S3-compatible storage")

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if client is not None:
            self.client = client
        else:
            client_kwargs = {
                "endpoint_url": endpoint_url,
                "aws_access_key_id": access_key_id,
                "aws_secret_access_key": secret_access_key,
                "aws_session_token": session_token,
                "region_name": region,
            }
            client_kwargs = {
                key: value for key, value in client_kwargs.items() if value is not None
            }
            self.client = boto3.client(
                "s3",
                config=Config(
                    signature_version="s3v4",
                    retries={"mode": "standard", "max_attempts": 3},
                ),
                **client_kwargs,
            )

    def _object_key(self, filename: str) -> str:
        if self.prefix:
            return f"{self.prefix}/{filename}"
        return filename

    async def save(self, image_path: str) -> str:
        path = Path(image_path)
        filename = path.name

        await asyncio.to_thread(
            self.client.upload_file,
            str(path),
            self.bucket,
            self._object_key(filename),
            ExtraArgs={"ContentType": _media_type(filename)},
        )
        await asyncio.to_thread(path.unlink, missing_ok=True)
        return filename

    async def get(self, image_id: str) -> StoredImage | None:
        filename = _safe_filename(image_id)
        if filename is None:
            return None

        try:
            response = await asyncio.to_thread(
                self.client.get_object,
                Bucket=self.bucket,
                Key=self._object_key(filename),
            )
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

        body = response["Body"]
        try:
            content = await asyncio.to_thread(body.read)
        finally:
            body.close()

        return StoredImage(
            media_type=response.get("ContentType") or _media_type(filename),
            content=content,
        )


def create_image_storage() -> ImageStorage:
    backend = os.getenv("STORAGE_BACKEND", "local").strip().lower()
    if backend == "local":
        return LocalImageStorage()
    if backend in {"s3", "r2"}:
        if not BOTO3_AVAILABLE:
            raise ValueError(
                "STORAGE_BACKEND=s3/r2 需要安装 boto3（pip install boto3）"
            )
        endpoint_url = os.getenv("S3_ENDPOINT_URL") or None
        if backend == "r2" and endpoint_url is None:
            raise ValueError("S3_ENDPOINT_URL is required when STORAGE_BACKEND=r2")
        return S3ImageStorage(
            bucket=os.getenv("S3_BUCKET", ""),
            endpoint_url=endpoint_url,
            access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or None,
            secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY") or None,
            session_token=os.getenv("AWS_SESSION_TOKEN") or None,
            region=os.getenv("AWS_DEFAULT_REGION") or None,
            prefix=os.getenv("S3_PREFIX", ""),
        )
    raise ValueError(
        f"Unsupported STORAGE_BACKEND: {backend}. Expected 'local', 's3', or 'r2'."
    )
