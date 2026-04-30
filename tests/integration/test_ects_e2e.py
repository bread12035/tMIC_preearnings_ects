"""End-to-end-ish test for ECTS (without a real Pub/Sub emulator).

Seeds fake GCS with the 4 sources and a mocked Claude, then invokes worker.handle.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ects.data_processor import ECTSDataProcessor
from ects.worker import ECTSWorker


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf)
    return buf.getvalue()


def test_ects_e2e_happy_path(fake_gcs) -> None:
    base = "company=AAPL/quarter=Q2/fiscal=2026/AAPL"
    fake_gcs.put_bytes(
        "bk-t",
        f"bbg/transcript/{base}.parquet",
        _parquet_bytes(pd.DataFrame({"text": ["Welcome to the call."]})),
    )
    fake_gcs.put_bytes(
        "bk-f",
        f"bbg/financial/{base}.parquet",
        _parquet_bytes(pd.DataFrame({"metric": ["revenue"], "value": [100.0]})),
    )
    fake_gcs.put_bytes(
        "bk-s",
        f"bbg/segment/{base}.parquet",
        _parquet_bytes(pd.DataFrame({"segment": ["iphone"], "revenue": [50.0]})),
    )
    fake_gcs.put_text(
        "bk-c",
        f"configs/ects/{base}.json",
        json.dumps({"sector": "tech"}),
    )

    processor = ECTSDataProcessor(
        gcs=fake_gcs,
        bucket_transcript="bk-t",
        prefix_transcript="bbg/transcript",
        bucket_financial="bk-f",
        prefix_financial="bbg/financial",
        bucket_segment="bk-s",
        prefix_segment="bbg/segment",
        bucket_config="bk-c",
        prefix_config="configs/ects",
    )

    claude = MagicMock()
    claude.complete = MagicMock(return_value="## Summary\nThe quarter was strong.")

    worker = ECTSWorker(
        processor=processor,
        claude=claude,
        gcs=fake_gcs,
        output_bucket="ects-out",
        output_prefix="digwork/tmic/ects_summary",
    )

    ok = worker.handle(
        {"ticker": "AAPL", "fiscal_year": "2026", "fiscal_quarter": "Q2"},
        {"message_id": "m-int-2"},
    )
    assert ok is True

    expected_path = (
        "digwork/tmic/ects_summary/company=AAPL/quarter=Q2/fiscal=2026/"
        "AAPL_FY_Q2_2026.md"
    )
    assert ("ects-out", expected_path) in fake_gcs.objects
    body = fake_gcs.objects[("ects-out", expected_path)].decode("utf-8")
    assert "strong" in body


def test_ects_e2e_missing_data_acks_without_output(fake_gcs) -> None:
    # Only seed transcript + config; financial and segment missing
    base = "company=AAPL/quarter=Q2/fiscal=2026/AAPL"
    fake_gcs.put_bytes(
        "bk-t",
        f"bbg/transcript/{base}.parquet",
        _parquet_bytes(pd.DataFrame({"text": ["x"]})),
    )
    fake_gcs.put_text(
        "bk-c", f"configs/ects/{base}.json", json.dumps({"sector": "tech"})
    )

    processor = ECTSDataProcessor(
        gcs=fake_gcs,
        bucket_transcript="bk-t",
        prefix_transcript="bbg/transcript",
        bucket_financial="bk-f",
        prefix_financial="bbg/financial",
        bucket_segment="bk-s",
        prefix_segment="bbg/segment",
        bucket_config="bk-c",
        prefix_config="configs/ects",
    )
    claude = MagicMock()
    claude.complete = MagicMock()
    worker = ECTSWorker(
        processor=processor,
        claude=claude,
        gcs=fake_gcs,
        output_bucket="ects-out",
        output_prefix="digwork/tmic/ects_summary",
    )

    ok = worker.handle(
        {"ticker": "AAPL", "fiscal_year": "2026", "fiscal_quarter": "Q2"},
        {},
    )
    assert ok is True  # ack despite missing data
    claude.complete.assert_not_called()
    # No output written
    assert not any(k[0] == "ects-out" for k in fake_gcs.objects.keys())


def test_ects_e2e_web_search_mode(fake_gcs) -> None:
    """When web_search_flag=True, skip GCS data pulls; just call Claude with the
    Stock Titan + Motley Fool prompt and write the summary."""
    # Processor must NOT be invoked.
    processor = MagicMock()
    processor.load_and_process = MagicMock(side_effect=AssertionError("called"))

    claude = MagicMock()
    claude.complete = MagicMock(return_value="## Summary\nGreat quarter from web.")

    worker = ECTSWorker(
        processor=processor,
        claude=claude,
        gcs=fake_gcs,
        output_bucket="ects-out",
        output_prefix="digwork/tmic/ects_summary",
        web_search_flag=True,
        web_search_max_uses=5,
        stocktitan_news_url="https://www.stocktitan.net/news",
        motley_fool_url="https://www.fool.com/earnings-call-transcripts",
    )

    ok = worker.handle(
        {"ticker": "AAPL", "fiscal_year": "2026", "fiscal_quarter": "Q2"}, {}
    )
    assert ok is True

    processor.load_and_process.assert_not_called()
    # Claude was called with web_search tool wired in
    assert claude.complete.call_count == 1
    call_kwargs = claude.complete.call_args.kwargs
    assert "tools" in call_kwargs and call_kwargs["tools"]
    assert call_kwargs["tools"][0]["name"] == "web_search"

    expected_path = (
        "digwork/tmic/ects_summary/company=AAPL/quarter=Q2/fiscal=2026/"
        "AAPL_FY_Q2_2026.md"
    )
    assert ("ects-out", expected_path) in fake_gcs.objects
    body = fake_gcs.objects[("ects-out", expected_path)].decode("utf-8")
    assert "Great quarter from web" in body
