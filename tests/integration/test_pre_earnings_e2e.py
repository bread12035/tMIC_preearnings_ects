"""End-to-end-ish test for pre-earnings (without a real Pub/Sub emulator).

We invoke worker.handle with a synthetic payload and assert the GCS write
happened. In the sync design the handler blocks until polling is complete,
so we just call it directly and check the result.

This exercises: worker -> monitor -> claude (mocked) -> gcs (fake)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from common.company_config import CompanyConfigLoader
from pre_earnings.monitor import PreEarningsMonitor
from pre_earnings.worker import PreEarningsWorker


def test_pre_earnings_e2e(fake_gcs) -> None:
    fake_gcs.put_json(
        "cfg-bucket",
        "configs/pre_earnings/AAPL.json",
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "press_release_urls": ["https://example.com"],
            "financial_topics": ["Revenue"],
            "polling": {
                "start_offset_minutes": 0,
                "interval_minutes": 0,
                "max_attempts": 3,
            },
            "summary_template": {"language": "en", "sections": ["S"]},
        },
    )

    cfg_loader = CompanyConfigLoader(fake_gcs, "cfg-bucket", "configs/pre_earnings")

    claude = MagicMock()
    # Simulate not-available on the first call, then a successful summary
    claude.complete = MagicMock(
        side_effect=["PRESS_RELEASE_NOT_AVAILABLE", "## Summary\nGreat quarter."]
    )

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
    worker = PreEarningsWorker(monitor)

    payload = {
        "ticker": "AAPL",
        "fiscal_year": "2026",
        "fiscal_quarter": "Q2",
        "event_time_iso": "2020-01-01T00:00:00+00:00",  # past => no wait
    }

    with patch("pre_earnings.monitor.time.sleep"):
        ok = worker.handle(payload, {"message_id": "m-int-1"})

    assert ok is True

    expected_path = (
        "digwork/tmic/pre_earnings_summary/company=AAPL/quarter=Q2/"
        "fiscal=2026/AAPL_FY_Q2_2026.md"
    )
    assert ("pe-out", expected_path) in fake_gcs.objects
    body = fake_gcs.objects[("pe-out", expected_path)].decode("utf-8")
    assert "Great quarter" in body
