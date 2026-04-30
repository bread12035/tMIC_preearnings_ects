"""Tests for event_calendar.scraper (sync)."""

from __future__ import annotations

import sys
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from event_calendar.models import WatchlistEntry
from event_calendar.scraper import EarningsCalendarScraper


def _entry(
    ticker: str = "AAPL",
    override: str | None = None,
) -> WatchlistEntry:
    return WatchlistEntry(
        ticker=ticker,
        company_name=f"{ticker} Inc.",
        fiscal_year="2026",
        fiscal_quarter="Q2",
        override_call_time=override,
    )


def _stub_yfinance(monkeypatch, calendar_value) -> None:
    """Install a fake yfinance module with Ticker(...).calendar = calendar_value.

    Pass calendar_value=Exception(...) to make the .calendar attribute raise.
    """
    fake_mod = types.ModuleType("yfinance")

    class _FakeTicker:
        def __init__(self, ticker: str) -> None:
            self.ticker = ticker

        @property
        def calendar(self):
            if isinstance(calendar_value, Exception):
                raise calendar_value
            return calendar_value

    fake_mod.Ticker = _FakeTicker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", fake_mod)


def test_manual_override_used_directly(monkeypatch) -> None:
    """When override_call_time is set, scraper bypasses yfinance + Claude."""
    claude = MagicMock()
    claude.complete = MagicMock(side_effect=AssertionError("must not call"))
    scraper = EarningsCalendarScraper(claude, web_search_max_uses=5)

    event = scraper.fetch(_entry(override="2026-04-28T21:00:00Z"))

    assert event is not None
    assert event.source == "manual"
    assert event.earnings_call_time == datetime(2026, 4, 28, 21, 0)
    claude.complete.assert_not_called()


def test_yfinance_with_precise_time_used(monkeypatch) -> None:
    _stub_yfinance(
        monkeypatch,
        {"Earnings Date": [datetime(2026, 4, 28, 21, 0)]},
    )
    claude = MagicMock()
    claude.complete = MagicMock(side_effect=AssertionError("must not call"))
    scraper = EarningsCalendarScraper(claude, web_search_max_uses=5)

    event = scraper.fetch(_entry())

    assert event is not None
    assert event.source == "yfinance"
    assert event.earnings_call_time == datetime(2026, 4, 28, 21, 0)


def test_yfinance_midnight_falls_back_to_web_search(monkeypatch) -> None:
    """yfinance returns midnight (date-only): scraper must fall back to Claude."""
    _stub_yfinance(
        monkeypatch,
        {"Earnings Date": [datetime(2026, 4, 28, 0, 0)]},
    )
    claude = MagicMock()
    claude.complete = MagicMock(
        return_value='{"earnings_call_time": "2026-04-28T21:00:00Z"}'
    )
    scraper = EarningsCalendarScraper(claude, web_search_max_uses=5)

    event = scraper.fetch(_entry())

    assert event is not None
    assert event.source == "web_search"
    assert event.earnings_call_time == datetime(2026, 4, 28, 21, 0)
    claude.complete.assert_called_once()


def test_all_sources_fail_returns_none(monkeypatch) -> None:
    _stub_yfinance(monkeypatch, RuntimeError("yfinance down"))
    claude = MagicMock()
    claude.complete = MagicMock(return_value="sorry, no result")
    scraper = EarningsCalendarScraper(claude, web_search_max_uses=5)

    event = scraper.fetch(_entry())
    assert event is None


def test_web_search_extracts_json_from_prose(monkeypatch) -> None:
    """Claude's web_search responses often wrap JSON in commentary."""
    _stub_yfinance(monkeypatch, RuntimeError("skip"))
    claude = MagicMock()
    claude.complete = MagicMock(
        return_value=(
            "Based on press releases, the call is at:\n"
            '```json\n{"earnings_call_time": "2026-04-28T21:00:00Z"}\n```'
        )
    )
    scraper = EarningsCalendarScraper(claude, web_search_max_uses=5)

    event = scraper.fetch(_entry())
    assert event is not None
    assert event.source == "web_search"


def test_watchlist_entry_rejects_non_utc_override() -> None:
    with pytest.raises(Exception):
        WatchlistEntry(
            ticker="AAPL",
            company_name="Apple Inc.",
            fiscal_year="2026",
            fiscal_quarter="Q2",
            override_call_time="2026-04-28T21:00:00-04:00",
        )


def test_watchlist_entry_accepts_z_suffix() -> None:
    e = WatchlistEntry(
        ticker="AAPL",
        company_name="Apple Inc.",
        fiscal_year="2026",
        fiscal_quarter="Q2",
        override_call_time="2026-04-28T21:00:00Z",
    )
    assert e.override_call_time == "2026-04-28T21:00:00Z"


def test_watchlist_entry_accepts_explicit_utc() -> None:
    e = WatchlistEntry(
        ticker="AAPL",
        company_name="Apple Inc.",
        fiscal_year="2026",
        fiscal_quarter="Q2",
        override_call_time="2026-04-28T21:00:00+00:00",
    )
    assert e.override_call_time == "2026-04-28T21:00:00+00:00"
