"""CronJob B: every 10 minutes, dispatch tasks whose execution time is due.

Reads the GCS task registry, finds pending tasks with
``execution_time <= now + dispatch_window``, publishes a Pub/Sub trigger,
and marks each task ``published``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from common.config import bootstrap_env, get_settings
from common.gcs_service import GCSService
from common.logging import setup_logging
from common.pubsub_publisher import AsyncPublisher
from event_calendar.models import ScheduledTask
from event_calendar.task_registry import TaskRegistry, utcnow


async def amain() -> None:
    bootstrap_env()
    settings = get_settings()
    setup_logging(settings.log_level)
    log = logging.getLogger(__name__)

    if settings.app_mode != "task_dispatcher":
        raise RuntimeError(
            f"task_dispatcher_main launched with APP_MODE={settings.app_mode!r}"
        )
    if not settings.event_calendar_registry_bucket:
        raise RuntimeError("EVENT_CALENDAR_REGISTRY_BUCKET must be set")

    log.info("startup", extra={"safe_settings": settings.safe_dict()})

    gcs = GCSService(
        settings.gcs_project_id, settings.gcs_custom_storage_endpoint
    )
    publisher = AsyncPublisher(
        settings.gcp_project_id, settings.gcp_pubsub_topic
    )
    registry = TaskRegistry(
        gcs,
        settings.event_calendar_registry_bucket,
        settings.event_calendar_registry_prefix,
    )

    dispatched = await run_dispatch(
        registry=registry,
        publisher=publisher,
        window_minutes=settings.event_calendar_dispatch_window_minutes,
    )
    log.info("task_dispatcher_complete", extra={"dispatched": dispatched})


async def run_dispatch(
    *,
    registry: TaskRegistry,
    publisher: AsyncPublisher,
    window_minutes: int,
    now: datetime | None = None,
) -> int:
    """Pure orchestration — extracted for unit testing."""
    log = logging.getLogger(__name__)

    now = now or utcnow()
    deadline = now + timedelta(minutes=window_minutes)

    quarters = await registry.list_quarter_files()
    dispatched = 0

    for fy, fq in quarters:
        tasks = await registry.load(fy, fq)
        for task in tasks:
            if task.status != "pending":
                continue
            exec_time = datetime.fromisoformat(task.execution_time_iso)
            if exec_time.tzinfo is not None:
                exec_time = exec_time.replace(tzinfo=None)
            if exec_time > deadline:
                continue

            try:
                await publisher.publish(
                    data={
                        "ticker": task.ticker,
                        "fiscal_year": task.fiscal_year,
                        "fiscal_quarter": task.fiscal_quarter,
                        "event_time_iso": task.event_time_iso,
                    },
                    attributes={"event_type": task.event_type},
                )
            except Exception:
                log.error(
                    "task_publish_failed",
                    extra={"task_id": task.task_id},
                    exc_info=True,
                )
                continue

            await registry.mark_published(task)
            dispatched += 1
            log.info(
                "task_dispatched",
                extra={
                    "task_id": task.task_id,
                    "event_type": task.event_type,
                    "execution_time": task.execution_time_iso,
                },
            )

    return dispatched


if __name__ == "__main__":
    asyncio.run(amain())
