"""Shared pytest fixtures.

The big idea: tests should never touch real GCS, real Pub/Sub, or real
Anthropic. We provide an in-memory FakeGCSService that satisfies the
GCSService interface (duck-typed - we use it wherever GCSService is expected),
plus settings/env helpers.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from common.exceptions import GCSObjectNotFound, GCSWriteError


# --- Fake GCS ---------------------------------------------------------------

class FakeGCSService:
    """In-memory sync replacement for GCSService."""

    def __init__(self) -> None:
        # Keyed by (bucket, blob_path)
        self.objects: dict[tuple[str, str], bytes] = {}
        self.write_errors: set[tuple[str, str]] = set()

    # --- helpers (test-side) ---
    def put_text(self, bucket: str, blob_path: str, content: str) -> None:
        self.objects[(bucket, blob_path)] = content.encode("utf-8")

    def put_bytes(self, bucket: str, blob_path: str, content: bytes) -> None:
        self.objects[(bucket, blob_path)] = content

    def put_json(self, bucket: str, blob_path: str, payload: Any) -> None:
        self.put_text(bucket, blob_path, json.dumps(payload))

    def fail_write(self, bucket: str, blob_path: str) -> None:
        self.write_errors.add((bucket, blob_path))

    # --- public sync API (mirrors GCSService) ---
    def read_bytes(self, bucket: str, blob_path: str) -> bytes:
        key = (bucket, blob_path)
        if key not in self.objects:
            raise GCSObjectNotFound(f"gs://{bucket}/{blob_path}")
        return self.objects[key]

    def read_text(
        self, bucket: str, blob_path: str, encoding: str = "utf-8"
    ) -> str:
        return self.read_bytes(bucket, blob_path).decode(encoding)

    def read_json(self, bucket: str, blob_path: str):
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
        if (bucket, blob_path) in self.write_errors:
            raise GCSWriteError(f"forced failure for gs://{bucket}/{blob_path}")
        self.objects[(bucket, blob_path)] = content.encode("utf-8")

    def write_json(self, bucket: str, blob_path: str, payload) -> None:
        self.write_text(
            bucket,
            blob_path,
            json.dumps(payload, indent=2, sort_keys=True),
            content_type="application/json; charset=utf-8",
        )

    def list_blobs(self, bucket: str, prefix: str) -> list[str]:
        return [
            path for (b, path) in self.objects.keys()
            if b == bucket and path.startswith(prefix)
        ]

    def exists(self, bucket: str, blob_path: str) -> bool:
        return (bucket, blob_path) in self.objects


@pytest.fixture
def fake_gcs() -> FakeGCSService:
    return FakeGCSService()


# --- Env / settings ---------------------------------------------------------

REQUIRED_ENV: dict[str, str] = {
    "APP_MODE": "pre_earnings",
    "LOG_LEVEL": "INFO",
    "ENVIRONMENT": "local",
    "GCP_PROJECT_ID": "test-project",
    "GCP_PUBSUB_TOPIC": "earnings-events",
    "GCP_PUBSUB_SUBSCRIPTION": "earnings-events-pre-earnings-sub",
    "GCP_PUBSUB_MAX_INFLIGHT": "5",
    "GCP_PUBSUB_ACK_DEADLINE_SECONDS": "60",
    "GCS_PROJECT_ID": "test-project",
    "GCS_BUCKET_PRE_EARNINGS_OUTPUT": "test-pe-out",
    "GCS_BLOB_PREFIX_PRE_EARNINGS_OUTPUT": "digwork/tmic/pre_earnings_summary",
    "GCS_BUCKET_ECTS_OUTPUT": "test-ects-out",
    "GCS_BLOB_PREFIX_ECTS_OUTPUT": "digwork/tmic/ects_summary",
    "GCS_BUCKET_ECTS_TRANSCRIPT": "test-bbg-transcript",
    "GCS_BLOB_PREFIX_ECTS_TRANSCRIPT": "bbg/transcript",
    "GCS_BUCKET_ECTS_FINANCIAL": "test-bbg-financial",
    "GCS_BLOB_PREFIX_ECTS_FINANCIAL": "bbg/financial",
    "GCS_BUCKET_ECTS_SEGMENT": "test-bbg-segment",
    "GCS_BLOB_PREFIX_ECTS_SEGMENT": "bbg/segment",
    "GCS_BUCKET_COMPANY_CONFIG": "test-company-config",
    "GCS_BLOB_PREFIX_PRE_EARNINGS_CONFIG": "configs/pre_earnings",
    "GCS_BLOB_PREFIX_ECTS_CONFIG": "configs/ects",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "ANTHROPIC_MODEL": "claude-test",
    "ANTHROPIC_MODEL_MAX_TOKENS": "1024",
    "ANTHROPIC_API_BASE_URL": "https://api.anthropic.com",
    "ANTHROPIC_REQUEST_TIMEOUT_SECONDS": "30",
    "ANTHROPIC_MAX_RETRIES": "3",
    "ANTHROPIC_RETRY_BASE_DELAY_SECONDS": "1",
    "ANTHROPIC_WEB_SEARCH_MAX_USES": "5",
    "PRE_EARNINGS_DEFAULT_START_OFFSET_MINUTES": "30",
    "PRE_EARNINGS_DEFAULT_POLL_INTERVAL_MINUTES": "10",
    "PRE_EARNINGS_DEFAULT_MAX_ATTEMPTS": "12",
    "EVENT_CALENDAR_WATCHLIST_BUCKET": "test-watchlist-bucket",
    "EVENT_CALENDAR_WATCHLIST_BLOB": "configs/watchlist.json",
    "EVENT_CALENDAR_REGISTRY_BUCKET": "test-registry-bucket",
    "EVENT_CALENDAR_REGISTRY_PREFIX": "configs/event_calendar",
    "EVENT_CALENDAR_LOOKAHEAD_DAYS": "14",
    "EVENT_CALENDAR_PRE_EARNINGS_OFFSET_MINUTES": "-30",
    "EVENT_CALENDAR_ECTS_OFFSET_MINUTES": "30",
    "EVENT_CALENDAR_DISPATCH_WINDOW_MINUTES": "10",
}


@pytest.fixture
def env_vars(monkeypatch):
    """Set all required env vars to test values."""
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)

    # Clear lru_cache so each test sees freshly-resolved settings
    from common import config as _cfg

    _cfg.get_settings.cache_clear()
    yield
    _cfg.get_settings.cache_clear()
