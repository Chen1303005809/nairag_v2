from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Protocol

from app.core.config import Settings


class AttachmentStorageError(Exception):
    """The attachment object store could not complete an operation."""


class AttachmentStorage(Protocol):
    async def initialize(self) -> None: ...

    async def put_object(self, key: str, content: bytes, content_type: str) -> None: ...

    async def get_object(self, key: str) -> bytes: ...

    async def delete_object(self, key: str) -> None: ...


class LocalAttachmentStorage:
    """Filesystem-backed storage used by development and integration tests."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _object_path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise AttachmentStorageError("attachment storage key is invalid")
        return candidate

    async def initialize(self) -> None:
        try:
            await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            raise AttachmentStorageError("无法初始化本地附件存储") from exc

    async def put_object(self, key: str, content: bytes, content_type: str) -> None:
        del content_type
        path = self._object_path(key)
        try:
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, content)
        except OSError as exc:
            raise AttachmentStorageError("无法保存附件") from exc

    async def get_object(self, key: str) -> bytes:
        path = self._object_path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise AttachmentStorageError("附件文件不存在") from exc
        except OSError as exc:
            raise AttachmentStorageError("无法读取附件") from exc

    async def delete_object(self, key: str) -> None:
        path = self._object_path(key)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError as exc:
            raise AttachmentStorageError("无法删除附件") from exc


class MinioAttachmentStorage:
    """Private MinIO bucket used by production API processes."""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool,
    ) -> None:
        try:
            from minio import Minio
        except ImportError as exc:  # pragma: no cover - exercised by production packaging.
            raise RuntimeError("MinIO storage requires the minio package") from exc
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self.bucket = bucket

    async def initialize(self) -> None:
        try:
            bucket_exists = await asyncio.to_thread(self.client.bucket_exists, self.bucket)
        except Exception as exc:  # noqa: BLE001 - third-party client errors have many concrete types.
            raise AttachmentStorageError("无法连接 MinIO 附件存储") from exc
        if not bucket_exists:
            raise AttachmentStorageError("MinIO 附件 Bucket 尚未初始化")

    async def put_object(self, key: str, content: bytes, content_type: str) -> None:
        def put() -> None:
            self.client.put_object(
                self.bucket,
                key,
                io.BytesIO(content),
                len(content),
                content_type=content_type,
            )

        try:
            await asyncio.to_thread(put)
        except Exception as exc:  # noqa: BLE001 - see initialize.
            raise AttachmentStorageError("无法保存附件到 MinIO") from exc

    async def get_object(self, key: str) -> bytes:
        def get() -> bytes:
            response = self.client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        try:
            return await asyncio.to_thread(get)
        except Exception as exc:  # noqa: BLE001 - see initialize.
            raise AttachmentStorageError("无法从 MinIO 读取附件") from exc

    async def delete_object(self, key: str) -> None:
        try:
            await asyncio.to_thread(self.client.remove_object, self.bucket, key)
        except Exception as exc:  # noqa: BLE001 - see initialize.
            raise AttachmentStorageError("无法从 MinIO 删除附件") from exc


def create_attachment_storage(settings: Settings) -> AttachmentStorage:
    if settings.attachment_storage_backend == "local":
        return LocalAttachmentStorage(settings.attachment_storage_dir)
    return MinioAttachmentStorage(
        endpoint=settings.attachment_minio_endpoint or "",
        access_key=settings.attachment_minio_access_key,
        secret_key=settings.attachment_minio_secret_key,
        bucket=settings.attachment_minio_bucket,
        secure=settings.attachment_minio_secure,
    )
