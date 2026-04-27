"""Tests for pre_earnings.worker.PreEarningsWorker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from pre_earnings.worker import PreEarningsWorker


@pytest.mark.asyncio
async def test_handle_acks_immediately_and_runs_polling_in_background() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_run(msg):
        started.set()
        await asyncio.sleep(0.05)
        finished.set()

    monitor = MagicMock()
    monitor.run = AsyncMock(side_effect=slow_run)

    worker = PreEarningsWorker(monitor)

    payload = {
        "ticker": "AAPL",
        "fiscal_year": "2026",
        "fiscal_quarter": "Q2",
        "event_time_iso": "2026-04-27T12:00:00+00:00",
    }

    ok = await worker.handle(payload, {"message_id": "m1"})
    assert ok is True

    # The handler returned True (ack) immediately. Polling has either started
    # or is about to; make sure it runs to completion when we yield.
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await asyncio.wait_for(finished.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_handle_malformed_message_acks() -> None:
    monitor = MagicMock()
    worker = PreEarningsWorker(monitor)
    ok = await worker.handle({"missing": "fields"}, {})
    assert ok is True
    monitor.run.assert_not_called() if hasattr(monitor.run, "assert_not_called") else None


@pytest.mark.asyncio
async def test_handle_swallows_monitor_exception() -> None:
    failed = asyncio.Event()

    async def boom(msg):
        failed.set()
        raise RuntimeError("monitor exploded")

    monitor = MagicMock()
    monitor.run = AsyncMock(side_effect=boom)
    worker = PreEarningsWorker(monitor)

    payload = {
        "ticker": "AAPL",
        "fiscal_year": "2026",
        "fiscal_quarter": "Q2",
        "event_time_iso": "2026-04-27T12:00:00+00:00",
    }

    ok = await worker.handle(payload, {})
    assert ok is True
    await asyncio.wait_for(failed.wait(), timeout=1.0)
    # Allow the background task to finalise (it should swallow the exception)
    await asyncio.sleep(0.01)
