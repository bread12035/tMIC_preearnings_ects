"""Tests for event_calendar.task_registry."""

from __future__ import annotations

import pytest

from event_calendar.models import ScheduledTask
from event_calendar.task_registry import TaskRegistry


BUCKET = "test-registry-bucket"
PREFIX = "configs/event_calendar"


def _task(
    ticker: str = "AAPL",
    fy: str = "2026",
    fq: str = "Q2",
    event_type: str = "pre_earnings",
    status: str = "pending",
) -> ScheduledTask:
    return ScheduledTask(
        task_id=ScheduledTask.make_id(ticker, fq, fy, event_type),
        event_type=event_type,
        ticker=ticker,
        fiscal_year=fy,
        fiscal_quarter=fq,
        event_time_iso="2026-04-28T21:00:00",
        execution_time_iso="2026-04-28T20:30:00",
        status=status,
    )


@pytest.mark.asyncio
async def test_load_missing_returns_empty(fake_gcs) -> None:
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    assert await reg.load("2026", "Q2") == []


@pytest.mark.asyncio
async def test_save_then_load_roundtrip(fake_gcs) -> None:
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    tasks = [_task(), _task(event_type="ects")]
    await reg.save("2026", "Q2", tasks)

    loaded = await reg.load("2026", "Q2")
    assert {t.task_id for t in loaded} == {t.task_id for t in tasks}


@pytest.mark.asyncio
async def test_upsert_dedups_by_task_id(fake_gcs) -> None:
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    initial = [_task(status="published")]
    await reg.save("2026", "Q2", initial)

    duplicate = _task(status="pending")
    inserted = await reg.upsert([duplicate])
    assert inserted == 0

    loaded = await reg.load("2026", "Q2")
    assert len(loaded) == 1
    # Existing 'published' status was preserved (never overwritten)
    assert loaded[0].status == "published"


@pytest.mark.asyncio
async def test_upsert_inserts_new_tasks(fake_gcs) -> None:
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    inserted = await reg.upsert([_task(), _task(event_type="ects")])
    assert inserted == 2
    loaded = await reg.load("2026", "Q2")
    assert len(loaded) == 2


@pytest.mark.asyncio
async def test_upsert_groups_by_quarter(fake_gcs) -> None:
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    tasks = [
        _task(ticker="AAPL", fy="2026", fq="Q2"),
        _task(ticker="MSFT", fy="2026", fq="Q3"),
    ]
    await reg.upsert(tasks)
    assert len(await reg.load("2026", "Q2")) == 1
    assert len(await reg.load("2026", "Q3")) == 1


@pytest.mark.asyncio
async def test_mark_published_idempotent(fake_gcs) -> None:
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    task = _task()
    await reg.save("2026", "Q2", [task])

    await reg.mark_published(task)
    await reg.mark_published(task)  # second call must not error

    loaded = await reg.load("2026", "Q2")
    assert loaded[0].status == "published"


@pytest.mark.asyncio
async def test_list_quarter_files(fake_gcs) -> None:
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    await reg.save("2026", "Q2", [_task(ticker="AAPL")])
    await reg.save("2026", "Q3", [_task(ticker="AAPL", fq="Q3")])
    await reg.save("2025", "Q4", [_task(ticker="AAPL", fq="Q4", fy="2025")])

    quarters = sorted(await reg.list_quarter_files())
    assert quarters == [("2025", "Q4"), ("2026", "Q2"), ("2026", "Q3")]


@pytest.mark.asyncio
async def test_list_quarter_files_ignores_unrelated_objects(fake_gcs) -> None:
    reg = TaskRegistry(fake_gcs, BUCKET, PREFIX)
    fake_gcs.put_text(BUCKET, f"{PREFIX}/README.md", "ignore me")
    fake_gcs.put_text(BUCKET, f"{PREFIX}/tasks_bad.json", "[]")
    await reg.save("2026", "Q2", [_task()])

    quarters = await reg.list_quarter_files()
    assert quarters == [("2026", "Q2")]
