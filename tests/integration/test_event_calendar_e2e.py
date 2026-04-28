"""End-to-end tests covering the calendar_sync + task_dispatcher pipeline.

Both components run against the in-memory FakeGCSService and a stubbed
publisher / Claude client — no real GCP or network IO.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from event_calendar.calendar_sync_main import run_sync
from event_calendar.scraper import EarningsCalendarScraper
from event_calendar.task_dispatcher_main import run_dispatch
from event_calendar.task_registry import TaskRegistry, utcnow


WATCHLIST_BUCKET = "test-watchlist-bucket"
WATCHLIST_BLOB = "configs/watchlist.json"
REGISTRY_BUCKET = "test-registry-bucket"
REGISTRY_PREFIX = "configs/event_calendar"


@pytest.mark.asyncio
async def test_calendar_sync_seeds_registry_from_watchlist(fake_gcs) -> None:
    """A watchlist with one manual-override entry should produce two
    ScheduledTasks (pre_earnings + ects) in the registry."""
    call_time = utcnow() + timedelta(days=2)
    fake_gcs.put_json(
        WATCHLIST_BUCKET,
        WATCHLIST_BLOB,
        [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "fiscal_year": "2026",
                "fiscal_quarter": "Q2",
                "override_call_time": call_time.isoformat() + "Z",
            }
        ],
    )

    claude = MagicMock()
    claude.complete = AsyncMock(side_effect=AssertionError("must not call"))
    scraper = EarningsCalendarScraper(claude, web_search_max_uses=5)
    registry = TaskRegistry(fake_gcs, REGISTRY_BUCKET, REGISTRY_PREFIX)

    inserted = await run_sync(
        gcs=fake_gcs,
        scraper=scraper,
        registry=registry,
        watchlist_bucket=WATCHLIST_BUCKET,
        watchlist_blob=WATCHLIST_BLOB,
        lookahead_days=14,
        pre_earnings_offset=-30,
        ects_offset=30,
    )
    assert inserted == 2

    tasks = await registry.load("2026", "Q2")
    by_type = {t.event_type: t for t in tasks}
    assert set(by_type) == {"pre_earnings", "ects"}

    # exec time = call_time + offset
    pre_exec = datetime.fromisoformat(by_type["pre_earnings"].execution_time_iso)
    ects_exec = datetime.fromisoformat(by_type["ects"].execution_time_iso)
    expected_call = call_time.replace(tzinfo=None)
    assert pre_exec == expected_call - timedelta(minutes=30)
    assert ects_exec == expected_call + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_dispatcher_publishes_seeded_tasks_when_due(fake_gcs) -> None:
    # Seed the registry via calendar_sync first
    call_time = (utcnow() + timedelta(minutes=5)).replace(microsecond=0)
    fake_gcs.put_json(
        WATCHLIST_BUCKET,
        WATCHLIST_BLOB,
        [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "fiscal_year": "2026",
                "fiscal_quarter": "Q2",
                "override_call_time": call_time.isoformat() + "Z",
            }
        ],
    )

    claude = MagicMock()
    claude.complete = AsyncMock()
    scraper = EarningsCalendarScraper(claude, web_search_max_uses=5)
    registry = TaskRegistry(fake_gcs, REGISTRY_BUCKET, REGISTRY_PREFIX)

    await run_sync(
        gcs=fake_gcs,
        scraper=scraper,
        registry=registry,
        watchlist_bucket=WATCHLIST_BUCKET,
        watchlist_blob=WATCHLIST_BLOB,
        lookahead_days=14,
        pre_earnings_offset=-30,
        ects_offset=30,
    )

    publisher = AsyncMock()
    publisher.publish = AsyncMock(return_value="msg-id")

    # call_time is 5 minutes from now; pre_earnings exec is call_time-30min
    # (already past), ECTS exec is call_time+30min (~35 min in future).
    # With a 60-minute window, both should publish.
    dispatched = await run_dispatch(
        registry=registry,
        publisher=publisher,
        window_minutes=60,
    )
    assert dispatched == 2
    assert publisher.publish.await_count == 2

    event_types = sorted(
        kw["attributes"]["event_type"]
        for _, kw in publisher.publish.call_args_list
    )
    assert event_types == ["ects", "pre_earnings"]

    # Re-running dispatch should be a no-op (status now 'published')
    publisher.publish.reset_mock()
    again = await run_dispatch(
        registry=registry, publisher=publisher, window_minutes=60
    )
    assert again == 0
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_calendar_sync_skips_events_beyond_lookahead(fake_gcs) -> None:
    """An earnings event further in the future than lookahead_days is dropped."""
    far = utcnow() + timedelta(days=60)
    fake_gcs.put_json(
        WATCHLIST_BUCKET,
        WATCHLIST_BLOB,
        [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "fiscal_year": "2026",
                "fiscal_quarter": "Q3",
                "override_call_time": far.isoformat() + "Z",
            }
        ],
    )

    claude = MagicMock()
    scraper = EarningsCalendarScraper(claude, web_search_max_uses=5)
    registry = TaskRegistry(fake_gcs, REGISTRY_BUCKET, REGISTRY_PREFIX)

    inserted = await run_sync(
        gcs=fake_gcs,
        scraper=scraper,
        registry=registry,
        watchlist_bucket=WATCHLIST_BUCKET,
        watchlist_blob=WATCHLIST_BLOB,
        lookahead_days=14,
        pre_earnings_offset=-30,
        ects_offset=30,
    )
    assert inserted == 0
