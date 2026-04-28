"""Tests for event_calendar.task_dispatcher_main.run_dispatch."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from event_calendar.models import ScheduledTask
from event_calendar.task_dispatcher_main import run_dispatch
from event_calendar.task_registry import TaskRegistry


BUCKET = "test-registry-bucket"
PREFIX = "configs/event_calendar"


def _task(
    ticker: str = "AAPL",
    event_type: str = "pre_earnings",
    exec_time: datetime | None = None,
    status: str = "pending",
) -> ScheduledTask:
    exec_time = exec_time or datetime(2026, 4, 28, 20, 30)
    return ScheduledTask(
        task_id=ScheduledTask.make_id(ticker, "Q2", "2026", event_type),
        event_type=event_type,
        ticker=ticker,
        fiscal_year="2026",
        fiscal_quarter="Q2",
        event_time_iso="2026-04-28T21:00:00",
        execution_time_iso=exec_time.isoformat(),
        status=status,
    )


@pytest.mark.asyncio
async def test_publishes_due_tasks_and_marks_published(fake_gcs) -> None:
    now = datetime(2026, 4, 28, 20, 25)  # task is due in 5 minutes (within window)
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    await reg.save("2026", "Q2", [_task()])

    publisher = AsyncMock()
    publisher.publish = AsyncMock(return_value="msg-1")

    dispatched = await run_dispatch(
        registry=reg,
        publisher=publisher,
        window_minutes=10,
        now=now,
    )

    assert dispatched == 1
    publisher.publish.assert_awaited_once()
    args, kwargs = publisher.publish.call_args
    assert kwargs["attributes"] == {"event_type": "pre_earnings"}
    assert kwargs["data"]["ticker"] == "AAPL"

    loaded = await reg.load("2026", "Q2")
    assert loaded[0].status == "published"


@pytest.mark.asyncio
async def test_skips_tasks_outside_window(fake_gcs) -> None:
    """exec_time is now+11min, dispatch_window=10min: must NOT publish."""
    now = datetime(2026, 4, 28, 20, 0)
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    await reg.save("2026", "Q2", [_task(exec_time=now + timedelta(minutes=11))])

    publisher = AsyncMock()
    publisher.publish = AsyncMock(return_value="msg-1")

    dispatched = await run_dispatch(
        registry=reg, publisher=publisher, window_minutes=10, now=now
    )

    assert dispatched == 0
    publisher.publish.assert_not_awaited()
    assert (await reg.load("2026", "Q2"))[0].status == "pending"


@pytest.mark.asyncio
async def test_window_boundary_inclusive(fake_gcs) -> None:
    """exec_time == now + window: at the boundary, the task IS dispatched."""
    now = datetime(2026, 4, 28, 20, 0)
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    await reg.save("2026", "Q2", [_task(exec_time=now + timedelta(minutes=10))])

    publisher = AsyncMock()
    publisher.publish = AsyncMock(return_value="msg-1")

    dispatched = await run_dispatch(
        registry=reg, publisher=publisher, window_minutes=10, now=now
    )
    assert dispatched == 1


@pytest.mark.asyncio
async def test_already_published_tasks_skipped(fake_gcs) -> None:
    now = datetime(2026, 4, 28, 20, 25)
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    await reg.save("2026", "Q2", [_task(status="published")])

    publisher = AsyncMock()
    publisher.publish = AsyncMock(return_value="msg-1")

    dispatched = await run_dispatch(
        registry=reg, publisher=publisher, window_minutes=10, now=now
    )

    assert dispatched == 0
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_failure_keeps_task_pending(fake_gcs) -> None:
    """If publish() raises, the task stays pending so the next cron retries."""
    now = datetime(2026, 4, 28, 20, 25)
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    await reg.save("2026", "Q2", [_task()])

    publisher = AsyncMock()
    publisher.publish = AsyncMock(side_effect=RuntimeError("pubsub down"))

    dispatched = await run_dispatch(
        registry=reg, publisher=publisher, window_minutes=10, now=now
    )

    assert dispatched == 0
    assert (await reg.load("2026", "Q2"))[0].status == "pending"


@pytest.mark.asyncio
async def test_multiple_quarters_handled(fake_gcs) -> None:
    now = datetime(2026, 4, 28, 20, 25)
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    await reg.save("2026", "Q2", [_task(ticker="AAPL")])
    # Different quarter file, also due now
    t = _task(ticker="MSFT")
    t = t.model_copy(update={"fiscal_quarter": "Q3"})
    await reg.save("2026", "Q3", [t])

    publisher = AsyncMock()
    publisher.publish = AsyncMock(return_value="msg-1")

    dispatched = await run_dispatch(
        registry=reg, publisher=publisher, window_minutes=10, now=now
    )

    assert dispatched == 2
    assert publisher.publish.await_count == 2
