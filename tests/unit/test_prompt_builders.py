"""Tests for prompt builders. Just snapshot key invariants."""

from __future__ import annotations

import pandas as pd

from common.company_config import PreEarningsCompanyConfig
from ects.models import ECTSProcessedData
from ects.prompt_builder import build_ects_prompt
from pre_earnings.models import PreEarningsMessage
from pre_earnings.prompt_builder import build_pre_earnings_prompt


def test_pre_earnings_prompt_includes_urls_and_sentinel():
    msg = PreEarningsMessage(
        ticker="AAPL",
        fiscal_year="2026",
        fiscal_quarter="Q2",
        event_time_iso="2026-04-27T12:00:00+00:00",
    )
    cfg = PreEarningsCompanyConfig(
        ticker="AAPL",
        company_name="Apple Inc.",
        press_release_urls=["https://www.apple.com/newsroom/"],
        financial_topics=["iPhone revenue"],
        summary_template={"language": "en", "sections": ["Headline"]},
    )

    system, user = build_pre_earnings_prompt(msg, cfg)
    assert "https://www.apple.com/newsroom/" in system
    assert "PRESS_RELEASE_NOT_AVAILABLE" in system
    assert "AAPL" in user
    assert "Q2" in user


def test_ects_prompt_renders_tables():
    data = ECTSProcessedData(
        ticker="AAPL",
        fiscal_year="2026",
        fiscal_quarter="Q2",
        transcript="Hello.",
        financial=pd.DataFrame({"metric": ["revenue"], "value": [100]}),
        segment=pd.DataFrame({"segment": ["iphone"], "revenue": [50]}),
        config={"sector": "tech"},
    )

    system, user = build_ects_prompt(data)
    assert "earnings call" in system.lower()
    assert "revenue" in user
    assert "iphone" in user
    assert "Hello." in user
