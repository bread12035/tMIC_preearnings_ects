"""Tests for pre_earnings.monitor.PreEarningsMonitor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.company_config import CompanyConfigLoader, PreEarningsCompanyConfig
from common.exceptions import (
    ClaudeAPIRetryExhaustedError,
    CompanyConfigNotFoundError,
)
from pre_earnings.models import PreEarningsMessage
from pre_earnings.monitor import PreEarningsMonitor


def _make_msg() -> PreEarningsMessage:
    # event_time in the past so _wait_until_start exits immediately
    return PreEarningsMessage(
        ticker="AAPL",
        fiscal_year="2026",
        fiscal_quarter="Q2",
        event_time_iso="2020-01-01T00:00:00+00:00",
    )


def _make_cfg(max_attempts: int = 3, interval: int = 1) -> PreEarningsCompanyConfig:
    return PreEarningsCompanyConfig(
        ticker="AAPL",
        company_name="Apple Inc.",
        press_release_urls=["https://example.com"],
        financial_topics=["x"],
        polling={
            "start_offset_minutes": 30,
            "interval_minutes": interval,
            "max_attempts": max_attempts,
        },
        summary_template={"language": "en", "sections": ["S1"]},
    )


@pytest.fixture
def monitor_pieces(fake_gcs):
    cfg_loader = MagicMock(spec=CompanyConfigLoader)
    cfg_loader.load_pre_earnings = AsyncMock(return_value=_make_cfg())

    claude = MagicMock()
    claude.complete = AsyncMock()

    monitor = PreEarningsMonitor(
        gcs=fake_gcs,
        claude=claude,
        config_loader=cfg_loader,
        output_bucket="pe-out",
        output_prefix="digwork/tmic/pre_earnings_summary",
        web_search_max_uses=5,
        stocktitan_news_url="https://www.stocktitan.net/news",
        prompt_system_path="prompts/pre_earnings_system.md.tmpl",
        prompt_user_path="prompts/pre_earnings_user.md.tmpl",
    )
    return monitor, cfg_loader, claude


@pytest.mark.asyncio
async def test_success_first_attempt_writes_gcs(monitor_pieces, fake_gcs) -> None:
    monitor, _, claude = monitor_pieces
    claude.complete.return_value = "## Summary\nGreat quarter."

    with patch("pre_earnings.monitor.asyncio.sleep", new=AsyncMock()):
        await monitor.run(_make_msg())

    expected_path = (
        "digwork/tmic/pre_earnings_summary/company=AAPL/quarter=Q2/"
        "fiscal=2026/AAPL_FY_Q2_2026.md"
    )
    assert ("pe-out", expected_path) in fake_gcs.objects
    claude.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_retries_on_not_found_then_succeeds(monitor_pieces, fake_gcs) -> None:
    monitor, _, claude = monitor_pieces
    claude.complete.side_effect = [
        "PRESS_RELEASE_NOT_AVAILABLE",
        "PRESS_RELEASE_NOT_AVAILABLE",
        "Final summary",
    ]

    with patch("pre_earnings.monitor.asyncio.sleep", new=AsyncMock()):
        await monitor.run(_make_msg())

    assert claude.complete.await_count == 3
    expected_path = (
        "digwork/tmic/pre_earnings_summary/company=AAPL/quarter=Q2/"
        "fiscal=2026/AAPL_FY_Q2_2026.md"
    )
    assert ("pe-out", expected_path) in fake_gcs.objects


@pytest.mark.asyncio
async def test_polling_exhausts_when_always_not_available(monitor_pieces, fake_gcs) -> None:
    monitor, _, claude = monitor_pieces
    claude.complete.return_value = "PRESS_RELEASE_NOT_AVAILABLE"

    with patch("pre_earnings.monitor.asyncio.sleep", new=AsyncMock()):
        await monitor.run(_make_msg())

    # max_attempts=3 default in fixture
    assert claude.complete.await_count == 3
    assert fake_gcs.objects == {}  # no output written


@pytest.mark.asyncio
async def test_claude_exhausted_treated_as_soft_fail(monitor_pieces, fake_gcs) -> None:
    monitor, _, claude = monitor_pieces
    # 1st: claude down. 2nd: still down. 3rd: success.
    claude.complete.side_effect = [
        ClaudeAPIRetryExhaustedError("down"),
        ClaudeAPIRetryExhaustedError("down again"),
        "Recovered summary",
    ]

    with patch("pre_earnings.monitor.asyncio.sleep", new=AsyncMock()):
        await monitor.run(_make_msg())

    assert claude.complete.await_count == 3
    expected_path = (
        "digwork/tmic/pre_earnings_summary/company=AAPL/quarter=Q2/"
        "fiscal=2026/AAPL_FY_Q2_2026.md"
    )
    assert ("pe-out", expected_path) in fake_gcs.objects


@pytest.mark.asyncio
async def test_missing_company_config_returns_quietly(monitor_pieces, fake_gcs) -> None:
    monitor, cfg_loader, claude = monitor_pieces
    cfg_loader.load_pre_earnings.side_effect = CompanyConfigNotFoundError("nope")

    await monitor.run(_make_msg())  # should not raise
    claude.complete.assert_not_awaited()
    assert fake_gcs.objects == {}
