"""Tests for common.company_config (sync)."""

from __future__ import annotations

import pytest

from common.company_config import CompanyConfigLoader
from common.exceptions import (
    CompanyConfigInvalidError,
    CompanyConfigNotFoundError,
)


def _valid_config() -> dict:
    return {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "press_release_urls": ["https://www.apple.com/newsroom/"],
        "financial_topics": ["iPhone revenue", "Services revenue"],
        "polling": {
            "start_offset_minutes": 30,
            "interval_minutes": 5,
            "max_attempts": 12,
        },
        "summary_template": {
            "language": "en",
            "sections": ["Headline numbers", "Margin"],
            "style_guidance": "Concise.",
        },
        "prompt_extras": {"additional_context": "..."},
    }


def test_load_pre_earnings_valid(fake_gcs) -> None:
    fake_gcs.put_json("cfg-bucket", "configs/pre_earnings/AAPL.json", _valid_config())
    loader = CompanyConfigLoader(fake_gcs, "cfg-bucket", "configs/pre_earnings")

    out = loader.load_pre_earnings("AAPL")
    assert out.ticker == "AAPL"
    assert out.polling.interval_minutes == 5
    assert out.summary_template.sections == ["Headline numbers", "Margin"]


def test_load_pre_earnings_missing(fake_gcs) -> None:
    loader = CompanyConfigLoader(fake_gcs, "cfg-bucket", "configs/pre_earnings")
    with pytest.raises(CompanyConfigNotFoundError):
        loader.load_pre_earnings("MISSING")


def test_load_pre_earnings_invalid(fake_gcs) -> None:
    # Missing the required `company_name` => invalid.
    fake_gcs.put_json("cfg-bucket", "configs/pre_earnings/BAD.json", {"ticker": "BAD"})
    loader = CompanyConfigLoader(fake_gcs, "cfg-bucket", "configs/pre_earnings")
    with pytest.raises(CompanyConfigInvalidError):
        loader.load_pre_earnings("BAD")


def test_load_pre_earnings_minimal_config_accepted(fake_gcs) -> None:
    """Just ticker + company_name is enough; the prompt builder falls back to
    Stock Titan with default topics/sections."""
    fake_gcs.put_json(
        "cfg-bucket",
        "configs/pre_earnings/MIN.json",
        {"ticker": "MIN", "company_name": "Minimal Co."},
    )
    loader = CompanyConfigLoader(fake_gcs, "cfg-bucket", "configs/pre_earnings")
    out = loader.load_pre_earnings("MIN")
    assert out.ticker == "MIN"
    assert out.press_release_urls == []
    assert out.financial_topics == []
    assert out.summary_template.sections == []
