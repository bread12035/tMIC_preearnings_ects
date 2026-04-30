"""Tests for ects.worker.ECTSWorker (sync). Verifies the exception -> ack/nack matrix."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from common.exceptions import (
    ClaudeAPIRetryExhaustedError,
    DataParseError,
    GCSWriteError,
    MissingDataError,
)
from ects.models import ECTSProcessedData
from ects.worker import ECTSWorker


def _payload() -> dict:
    return {"ticker": "AAPL", "fiscal_year": "2026", "fiscal_quarter": "Q2"}


def _processed() -> ECTSProcessedData:
    return ECTSProcessedData(
        ticker="AAPL",
        fiscal_year="2026",
        fiscal_quarter="Q2",
        transcript="Hello.",
        financial=pd.DataFrame({"m": ["rev"], "v": [1]}),
        segment=pd.DataFrame({"s": ["x"], "v": [2]}),
        config={"sector": "tech"},
    )


def _make_worker(fake_gcs):
    processor = MagicMock()
    processor.load_and_process = MagicMock(return_value=_processed())
    claude = MagicMock()
    claude.complete = MagicMock(return_value="## Summary")

    worker = ECTSWorker(
        processor=processor,
        claude=claude,
        gcs=fake_gcs,
        output_bucket="ects-out",
        output_prefix="digwork/tmic/ects_summary",
    )
    return worker, processor, claude


def test_happy_path_writes_and_acks(fake_gcs) -> None:
    worker, _, claude = _make_worker(fake_gcs)
    ok = worker.handle(_payload(), {"message_id": "m1"})
    assert ok is True
    expected_path = (
        "digwork/tmic/ects_summary/company=AAPL/quarter=Q2/fiscal=2026/"
        "AAPL_FY_Q2_2026.md"
    )
    assert ("ects-out", expected_path) in fake_gcs.objects
    claude.complete.assert_called_once()


def test_malformed_payload_acks(fake_gcs) -> None:
    worker, processor, _ = _make_worker(fake_gcs)
    ok = worker.handle({"missing": "fields"}, {})
    assert ok is True
    processor.load_and_process.assert_not_called()


def test_missing_data_acks(fake_gcs) -> None:
    worker, processor, _ = _make_worker(fake_gcs)
    processor.load_and_process.side_effect = MissingDataError("AAPL", ["financial"])
    ok = worker.handle(_payload(), {})
    assert ok is True


def test_data_parse_error_acks(fake_gcs) -> None:
    worker, processor, _ = _make_worker(fake_gcs)
    processor.load_and_process.side_effect = DataParseError("bad parquet")
    ok = worker.handle(_payload(), {})
    assert ok is True


def test_claude_exhausted_acks(fake_gcs) -> None:
    worker, _, claude = _make_worker(fake_gcs)
    claude.complete.side_effect = ClaudeAPIRetryExhaustedError("down")
    ok = worker.handle(_payload(), {})
    assert ok is True
    # No output written
    assert fake_gcs.objects == {}


def test_gcs_write_failure_nacks(fake_gcs) -> None:
    worker, _, _ = _make_worker(fake_gcs)
    fake_gcs.fail_write(
        "ects-out",
        "digwork/tmic/ects_summary/company=AAPL/quarter=Q2/fiscal=2026/AAPL_FY_Q2_2026.md",
    )
    ok = worker.handle(_payload(), {})
    assert ok is False  # nack -> Pub/Sub redelivers
