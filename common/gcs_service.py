"""Async GCS read/write wrapper around the sync google-cloud-storage client."""

from __future__ import annotations

import asyncio
import json

from google.cloud import storage
from google.cloud.exceptions import NotFound

from common.exceptions import GCSObjectNotFound, GCSWriteError


class GCSService:
    """
    Wraps google-cloud-storage (sync client) with asyncio.to_thread for non-blocking IO.
    Uses Workload Identity for auth (no key file).
    """

    def __init__(self, project_id: str, endpoint: str | None = None):
        self._client = storage.Client(
            project=project_id,
            client_options={"api_endpoint": endpoint} if endpoint else None,
        )

    async def read_bytes(self, bucket: str, blob_path: str) -> bytes:
        """Raise GCSObjectNotFound if blob does not exist."""
        return await asyncio.to_thread(self._read_bytes_sync, bucket, blob_path)

    async def read_text(
        self, bucket: str, blob_path: str, encoding: str = "utf-8"
    ) -> str:
        data = await self.read_bytes(bucket, blob_path)
        return data.decode(encoding)

    async def read_json(self, bucket: str, blob_path: str) -> dict:
        text = await self.read_text(bucket, blob_path)
        return json.loads(text)

    async def read_parquet_bytes(self, bucket: str, blob_path: str) -> bytes:
        return await self.read_bytes(bucket, blob_path)

    async def write_text(
        self,
        bucket: str,
        blob_path: str,
        content: str,
        content_type: str = "text/markdown; charset=utf-8",
    ) -> None:
        await asyncio.to_thread(
            self._write_text_sync, bucket, blob_path, content, content_type
        )

    async def write_json(self, bucket: str, blob_path: str, payload) -> None:
        await self.write_text(
            bucket,
            blob_path,
            json.dumps(payload, indent=2, sort_keys=True),
            content_type="application/json; charset=utf-8",
        )

    async def list_blobs(self, bucket: str, prefix: str) -> list[str]:
        return await asyncio.to_thread(self._list_blobs_sync, bucket, prefix)

    async def exists(self, bucket: str, blob_path: str) -> bool:
        return await asyncio.to_thread(self._exists_sync, bucket, blob_path)

    # --- sync impls (private) ---
    def _read_bytes_sync(self, bucket: str, blob_path: str) -> bytes:
        try:
            blob = self._client.bucket(bucket).blob(blob_path)
            return blob.download_as_bytes()
        except NotFound as e:
            raise GCSObjectNotFound(f"gs://{bucket}/{blob_path}") from e

    def _write_text_sync(
        self, bucket: str, blob_path: str, content: str, content_type: str
    ) -> None:
        try:
            blob = self._client.bucket(bucket).blob(blob_path)
            blob.upload_from_string(content, content_type=content_type)
        except Exception as e:
            raise GCSWriteError(
                f"Failed to write gs://{bucket}/{blob_path}: {e}"
            ) from e

    def _exists_sync(self, bucket: str, blob_path: str) -> bool:
        return self._client.bucket(bucket).blob(blob_path).exists()

    def _list_blobs_sync(self, bucket: str, prefix: str) -> list[str]:
        return [
            blob.name
            for blob in self._client.list_blobs(bucket, prefix=prefix)
        ]
