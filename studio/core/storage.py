"""物件儲存抽象。

提供兩種後端：
- `s3`：S3 相容服務（MinIO / AWS S3 / RustFS），production 使用。
- `local`：寫入本機目錄，讓開發者不必先跑起 MinIO。

上層（檔案服務、生成任務）只依賴 `ObjectStorage` 介面，不直接使用 boto3，
因此更換儲存供應商不需要改動業務程式。
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from studio.config import Settings, StorageBackend, get_settings, resolve_secret
from studio.core.errors import ConfigurationError, StudioError


class ObjectStorage(ABC):
    """物件儲存介面。"""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        """寫入位元組內容，回傳可供讀取的 key。"""

    @abstractmethod
    def put_stream(self, key: str, stream: BinaryIO, *, content_type: str = "application/octet-stream") -> str:
        """從串流寫入，避免大檔全部載入記憶體。"""

    @abstractmethod
    def get_bytes(self, key: str) -> bytes:
        """讀取物件內容。"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """刪除物件；物件不存在時視為成功（冪等）。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """檢查物件是否存在。"""

    @abstractmethod
    def public_url(self, key: str) -> str:
        """回傳可供前端讀取的 URL。"""

    @abstractmethod
    def healthy(self) -> bool:
        """儲存後端是否可用，供 health endpoint 使用。"""


class LocalObjectStorage(ObjectStorage):
    """以本機檔案系統實作的儲存後端（開發用）。"""

    def __init__(self, root: Path, public_base_url: str = "") -> None:
        self._root = root
        self._public_base_url = public_base_url.rstrip("/")
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """把 key 轉為實體路徑，並阻擋路徑穿越。"""

        cleaned = key.strip().lstrip("/")
        if not cleaned:
            raise StudioError("storage key must not be empty")
        target = (self._root / cleaned).resolve()
        # 防止 `../` 穿越到 root 之外。
        if not str(target).startswith(str(self._root)):
            raise StudioError("invalid storage key")
        return target

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return key

    def put_stream(self, key: str, stream: BinaryIO, *, content_type: str = "application/octet-stream") -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            shutil.copyfileobj(stream, handle)
        return key

    def get_bytes(self, key: str) -> bytes:
        target = self._path(key)
        if not target.exists():
            raise StudioError(f"object not found: {key}", code="object_not_found", status_code=404)
        return target.read_bytes()

    def delete(self, key: str) -> None:
        target = self._path(key)
        target.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def public_url(self, key: str) -> str:
        if self._public_base_url:
            return f"{self._public_base_url}/{key.lstrip('/')}"
        # 未設定公開前綴時，交由 API 端點代為串流。
        return f"/api/v1/studio/files/content/{key.lstrip('/')}"

    def healthy(self) -> bool:
        return self._root.is_dir()


class S3ObjectStorage(ObjectStorage):
    """S3 相容儲存後端。

    金鑰只從環境變數讀取（變數名稱由設定指定），不接受直接傳入明文。
    """

    def __init__(self, settings: Settings) -> None:
        import boto3  # 延遲匯入：local 後端不需要付出 boto3 的載入成本
        from botocore.config import Config

        access_key = resolve_secret(settings.s3_access_key_env)
        secret_key = resolve_secret(settings.s3_secret_key_env)
        if not access_key or not secret_key:
            raise ConfigurationError(
                "S3 credentials are not configured; "
                f"set {settings.s3_access_key_env} and {settings.s3_secret_key_env}"
            )

        self._bucket = settings.s3_bucket_name
        self._public_base_url = settings.storage_public_base_url.rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region_name,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        )

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def put_stream(self, key: str, stream: BinaryIO, *, content_type: str = "application/octet-stream") -> str:
        self._client.upload_fileobj(
            stream,
            self._bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        return key

    def get_bytes(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False

    def public_url(self, key: str) -> str:
        if self._public_base_url:
            return f"{self._public_base_url}/{key.lstrip('/')}"
        return f"/api/v1/studio/files/content/{key.lstrip('/')}"

    def healthy(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True
        except Exception:
            return False

    def ensure_bucket(self) -> None:
        """建立 bucket（若不存在），供初始化腳本使用。"""

        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    """取得（必要時建立）全域儲存實例。"""

    global _storage
    if _storage is None:
        settings = get_settings()
        if settings.storage_backend is StorageBackend.s3:
            _storage = S3ObjectStorage(settings)
        else:
            _storage = LocalObjectStorage(settings.local_storage_path, settings.storage_public_base_url)
    return _storage


def reset_storage() -> None:
    """清除快取的儲存實例（測試用）。"""

    global _storage
    _storage = None
