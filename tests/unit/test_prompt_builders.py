"""Tests for prompt builders. Just snapshot key invariants."""

from __future__ import annotations

import pandas as pd

from common.company_config import PreEarningsCompanyConfig
from ects.models import ECTSMessage, ECTSProcessedData
from ects.prompt_builder import build_ects_prompt, build_ects_web_search_prompt
from pre_earnings.models import PreEarningsMessage
from pre_earnings.prompt_builder import build_pre_earnings_prompt

PRE_SYS = "prompts/pre_earnings_system.md.tmpl"
PRE_USER = "prompts/pre_earnings_user.md.tmpl"
ECTS_SYS = "prompts/ects_system.md.tmpl"
ECTS_USER = "prompts/ects_user.md.tmpl"
ECTS_WS_SYS = "prompts/ects_web_search_system.md.tmpl"
ECTS_WS_USER = "prompts/ects_web_search_user.md.tmpl"
ECTS_WS_TMPL = "prompts/ects_web_search_template.md.tmpl"

STOCKTITAN = "https://www.stocktitan.net/news"
MOTLEY = "https://www.fool.com/earnings-call-transcripts"


def _msg() -> PreEarningsMessage:
    return PreEarningsMessage(
        ticker="AAPL",
        fiscal_year="2026",
        fiscal_quarter="Q2",
        event_time_iso="2026-04-27T12:00:00+00:00",
    )


def test_pre_earnings_prompt_uses_stocktitan_and_includes_fallback():
    cfg = PreEarningsCompanyConfig(
        ticker="AAPL",
        company_name="Apple Inc.",
        press_release_urls=["https://www.apple.com/newsroom/"],
        financial_topics=["iPhone revenue"],
        summary_template={"language": "en", "sections": ["Headline"]},
    )

    system, user = build_pre_earnings_prompt(
        _msg(),
        cfg,
        stocktitan_news_url=STOCKTITAN,
        system_template_path=PRE_SYS,
        user_template_path=PRE_USER,
    )

    # Stock Titan is primary
    assert STOCKTITAN in system
    # Fallback IR url is preserved when present in cfg
    assert "https://www.apple.com/newsroom/" in system
    # Sentinel is referenced
    assert "PRESS_RELEASE_NOT_AVAILABLE" in system
    # Ticker / quarter make it through to user prompt
    assert "AAPL" in user
    assert "Q2" in user
    # Topic from config rendered in user
    assert "iPhone revenue" in user


def test_pre_earnings_prompt_works_with_minimal_config():
    """Just ticker + company_name + msg should be enough to query Stock Titan."""
    cfg = PreEarningsCompanyConfig(ticker="AAPL", company_name="Apple Inc.")

    system, user = build_pre_earnings_prompt(
        _msg(),
        cfg,
        stocktitan_news_url=STOCKTITAN,
        system_template_path=PRE_SYS,
        user_template_path=PRE_USER,
    )

    # No fallback URLs to advertise, so the fallback section is omitted.
    assert "FALLBACK SOURCES" not in system
    # But Stock Titan and the sentinel are still there
    assert STOCKTITAN in system
    assert "PRESS_RELEASE_NOT_AVAILABLE" in system
    # Default topics + sections fill in
    assert "Revenue" in user
    assert "Headline numbers" in user


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

    system, user = build_ects_prompt(
        data,
        system_template_path=ECTS_SYS,
        user_template_path=ECTS_USER,
    )
    assert "earnings call" in system.lower()
    assert "revenue" in user
    assert "iphone" in user
    assert "Hello." in user


def test_ects_web_search_prompt_includes_both_sources():
    msg = ECTSMessage(ticker="AAPL", fiscal_year="2026", fiscal_quarter="Q2")
    system, user = build_ects_web_search_prompt(
        msg,
        company_name="Apple Inc.",
        stocktitan_news_url=STOCKTITAN,
        motley_fool_url=MOTLEY,
        system_template_path=ECTS_WS_SYS,
        user_template_path=ECTS_WS_USER,
        template_path=ECTS_WS_TMPL,
    )

    assert STOCKTITAN in system
    assert MOTLEY in system
    assert "ECTS_SOURCES_NOT_AVAILABLE" in system
    # User prompt embeds the inner template (rendered with quarter info)
    assert "Earnings Call Summary" in user
    assert "AAPL" in user and "Q2" in user and "2026" in user
    assert STOCKTITAN in user
    assert MOTLEY in user
