"""Tests for common.gcs_service. Uses mocked storage.Client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from google.cloud.exceptions import NotFound

from common.exceptions import GCSObjectNotFound, GCSWriteError
from common.gcs_service import GCSService


def _make_service_with_mocked_client():
    svc = GCSService.__new__(GCSService)  # bypass real client init
    mock_client = MagicMock()
    svc._client = mock_client
    return svc, mock_client


@pytest.mark.asyncio
async def test_read_bytes_returns_blob_payload() -> None:
    svc, mock_client = _make_service_with_mocked_client()
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = b"hello"
    mock_client.bucket.return_value.blob.return_value = mock_blob

    out = await svc.read_bytes("bk", "path")
    assert out == b"hello"
    mock_client.bucket.assert_called_with("bk")


@pytest.mark.asyncio
async def test_read_bytes_maps_notfound_to_gcsnotfound() -> None:
    svc, mock_client = _make_service_with_mocked_client()
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.side_effect = NotFound("missing")
    mock_client.bucket.return_value.blob.return_value = mock_blob

    with pytest.raises(GCSObjectNotFound):
        await svc.read_bytes("bk", "missing")


@pytest.mark.asyncio
async def test_write_text_uploads() -> None:
    svc, mock_client = _make_service_with_mocked_client()
    mock_blob = MagicMock()
    mock_client.bucket.return_value.blob.return_value = mock_blob

    await svc.write_text("bk", "path", "hi")
    mock_blob.upload_from_string.assert_called_once()
    args, kwargs = mock_blob.upload_from_string.call_args
    assert args[0] == "hi"


@pytest.mark.asyncio
async def test_write_text_wraps_errors() -> None:
    svc, mock_client = _make_service_with_mocked_client()
    mock_blob = MagicMock()
    mock_blob.upload_from_string.side_effect = RuntimeError("boom")
    mock_client.bucket.return_value.blob.return_value = mock_blob

    with pytest.raises(GCSWriteError):
        await svc.write_text("bk", "path", "x")


@pytest.mark.asyncio
async def test_read_json_round_trip() -> None:
    svc, mock_client = _make_service_with_mocked_client()
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = b'{"k": 1}'
    mock_client.bucket.return_value.blob.return_value = mock_blob

    out = await svc.read_json("bk", "p")
    assert out == {"k": 1}


@pytest.mark.asyncio
async def test_exists_calls_blob_exists() -> None:
    svc, mock_client = _make_service_with_mocked_client()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_client.bucket.return_value.blob.return_value = mock_blob

    assert (await svc.exists("bk", "p")) is True
