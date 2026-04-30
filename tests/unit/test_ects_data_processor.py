"""Tests for ects.data_processor.ECTSDataProcessor (sync)."""

from __future__ import annotations

import io
import json

import pandas as pd
import pytest

from common.exceptions import DataParseError, MissingDataError
from ects.data_processor import ECTSDataProcessor
from ects.models import ECTSMessage


def _make_processor(fake_gcs) -> ECTSDataProcessor:
    return ECTSDataProcessor(
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


def _msg() -> ECTSMessage:
    return ECTSMessage(ticker="AAPL", fiscal_year="2026", fiscal_quarter="Q2")


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf)
    return buf.getvalue()


def _seed_all(fake_gcs) -> None:
    fake_gcs.put_bytes(
        "bk-t",
        "bbg/transcript/company=AAPL/quarter=Q2/fiscal=2026/AAPL.parquet",
        _parquet_bytes(pd.DataFrame({"text": ["Welcome to the earnings call."]})),
    )
    fake_gcs.put_bytes(
        "bk-f",
        "bbg/financial/company=AAPL/quarter=Q2/fiscal=2026/AAPL.parquet",
        _parquet_bytes(pd.DataFrame({"metric": ["revenue"], "value": [100.0]})),
    )
    fake_gcs.put_bytes(
        "bk-s",
        "bbg/segment/company=AAPL/quarter=Q2/fiscal=2026/AAPL.parquet",
        _parquet_bytes(pd.DataFrame({"segment": ["iphone"], "revenue": [50.0]})),
    )
    fake_gcs.put_text(
        "bk-c",
        "configs/ects/company=AAPL/quarter=Q2/fiscal=2026/AAPL.json",
        json.dumps({"sector": "tech"}),
    )


def test_load_and_process_happy_path(fake_gcs) -> None:
    _seed_all(fake_gcs)
    processor = _make_processor(fake_gcs)

    out = processor.load_and_process(_msg())
    assert out.ticker == "AAPL"
    assert "Welcome" in out.transcript
    assert list(out.financial.columns) == ["metric", "value"]
    assert list(out.segment.columns) == ["segment", "revenue"]
    assert out.config == {"sector": "tech"}


def test_missing_data_raises_with_sources(fake_gcs) -> None:
    # Seed only 2 of 4
    _seed_all(fake_gcs)
    del fake_gcs.objects[
        ("bk-f", "bbg/financial/company=AAPL/quarter=Q2/fiscal=2026/AAPL.parquet")
    ]
    del fake_gcs.objects[
        ("bk-s", "bbg/segment/company=AAPL/quarter=Q2/fiscal=2026/AAPL.parquet")
    ]
    processor = _make_processor(fake_gcs)

    with pytest.raises(MissingDataError) as ei:
        processor.load_and_process(_msg())
    assert ei.value.ticker == "AAPL"
    assert set(ei.value.missing_sources) == {"financial", "segment"}


def test_data_parse_error_on_bad_parquet(fake_gcs) -> None:
    _seed_all(fake_gcs)
    fake_gcs.put_bytes(
        "bk-f",
        "bbg/financial/company=AAPL/quarter=Q2/fiscal=2026/AAPL.parquet",
        b"this is not a parquet file",
    )
    processor = _make_processor(fake_gcs)

    with pytest.raises(DataParseError):
        processor.load_and_process(_msg())


def test_data_parse_error_on_bad_config_json(fake_gcs) -> None:
    _seed_all(fake_gcs)
    fake_gcs.put_text(
        "bk-c",
        "configs/ects/company=AAPL/quarter=Q2/fiscal=2026/AAPL.json",
        "{not json",
    )
    processor = _make_processor(fake_gcs)

    with pytest.raises(DataParseError):
        processor.load_and_process(_msg())
