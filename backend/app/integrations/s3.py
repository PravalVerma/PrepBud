"""S3-compatible object storage for uploaded documents (AWS S3, Cloudflare R2, MinIO).

Uploads go browser → S3 directly via a presigned PUT (ARCHITECTURE §2.1); the API
only signs URLs, verifies the object after upload, and reads/deletes it.
boto3 is synchronous, so network calls run in a worker thread.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client


class StorageError(Exception):
    """Object storage failed in a way that may succeed on retry."""


class ObjectTooLargeError(StorageError):
    pass


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size: int
    content_type: str | None


def _client(settings: Settings, endpoint_url: str | None) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value() or None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if endpoint_url else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
        ),
    )


class ObjectStorage:
    def __init__(
        self,
        settings: Settings,
        *,
        client: S3Client | None = None,
        presign_client: S3Client | None = None,
    ) -> None:
        self.bucket = settings.s3_bucket
        self._client = client or _client(settings, settings.s3_endpoint_url)
        public = settings.s3_public_endpoint_url
        self._presign = presign_client or (
            _client(settings, public)
            if public and public != settings.s3_endpoint_url
            else self._client
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> ObjectStorage | None:
        """``None`` when storage isn't configured (uploads then answer 503)."""
        return cls(settings) if settings.s3_bucket else None

    def presign_put(self, key: str, content_type: str, expires_in: int) -> str:
        """Presigned PUT URL; the browser must send the same ``Content-Type``."""
        return self._presign.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )

    async def head(self, key: str) -> ObjectInfo | None:
        def _head() -> ObjectInfo | None:
            try:
                resp = self._client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise StorageError(f"HEAD failed: {exc}") from exc
            except BotoCoreError as exc:
                raise StorageError(f"HEAD failed: {exc}") from exc
            return ObjectInfo(size=int(resp["ContentLength"]), content_type=resp.get("ContentType"))

        return await asyncio.to_thread(_head)

    async def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        def _get() -> bytes:
            try:
                resp = self._client.get_object(Bucket=self.bucket, Key=key)
                body: Any = resp["Body"]
                data: bytes = body.read(max_bytes + 1)
                body.close()
            except (ClientError, BotoCoreError) as exc:
                raise StorageError(f"GET failed: {exc}") from exc
            if len(data) > max_bytes:
                raise ObjectTooLargeError(f"Object exceeds {max_bytes} bytes")
            return data

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            try:
                self._client.delete_object(Bucket=self.bucket, Key=key)
            except (ClientError, BotoCoreError) as exc:
                raise StorageError(f"DELETE failed: {exc}") from exc

        await asyncio.to_thread(_delete)
