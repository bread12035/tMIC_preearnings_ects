"""Sync GCS read/write wrapper around the google-cloud-storage client."""

from __future__ import annotations

import json

from google.cloud import storage
from google.cloud.exceptions import NotFound

from common.exceptions import GCSObjectNotFound, GCSWriteError


class GCSService:
    """
    Sync GCS wrapper using the official google-cloud-storage client.
    Uses Workload Identity for auth (no key file needed).
    """

    def __init__(self, project_id: str, endpoint: str | None = None):
        self._client = storage.Client(
            project=project_id,
            client_options={"api_endpoint": endpoint} if endpoint else None,
        )

    def read_bytes(self, bucket: str, blob_path: str) -> bytes:
        """Raise GCSObjectNotFound if blob does not exist."""
        try:
            return self._client.bucket(bucket).blob(blob_path).download_as_bytes()
        except NotFound as e:
            raise GCSObjectNotFound(f"gs://{bucket}/{blob_path}") from e

    def read_text(
        self, bucket: str, blob_path: str, encoding: str = "utf-8"
    ) -> str:
        return self.read_bytes(bucket, blob_path).decode(encoding)

    def read_json(self, bucket: str, blob_path: str) -> dict:
        return json.loads(self.read_text(bucket, blob_path))

    def read_parquet_bytes(self, bucket: str, blob_path: str) -> bytes:
        return self.read_bytes(bucket, blob_path)

    def write_text(
        self,
        bucket: str,
        blob_path: str,
        content: str,
        content_type: str = "text/markdown; charset=utf-8",
    ) -> None:
        try:
            self._client.bucket(bucket).blob(blob_path).upload_from_string(
                content, content_type=content_type
            )
        except Exception as e:
            raise GCSWriteError(
                f"Failed to write gs://{bucket}/{blob_path}: {e}"
            ) from e

    def write_json(self, bucket: str, blob_path: str, payload) -> None:
        self.write_text(
            bucket,
            blob_path,
            json.dumps(payload, indent=2, sort_keys=True),
            content_type="application/json; charset=utf-8",
        )

    def exists(self, bucket: str, blob_path: str) -> bool:
        return self._client.bucket(bucket).blob(blob_path).exists()

    def list_blobs(self, bucket: str, prefix: str) -> list[str]:
        return [
            blob.name
            for blob in self._client.list_blobs(bucket, prefix=prefix)
        ]
