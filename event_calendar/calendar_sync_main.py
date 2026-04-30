"""CronJob A: weekly calendar sync (sync).

Reads the GCS watchlist, resolves each ticker's earnings call time, and
upserts (does not overwrite) the per-quarter task registry.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from common.claude_client import ClaudeClient
from common.config import bootstrap_env, get_settings
from common.gcs_service import GCSService
from common.logging import setup_logging
from event_calendar.models import ScheduledTask, WatchlistEntry
from event_calendar.scraper import EarningsCalendarScraper
from event_calendar.task_registry import TaskRegistry, utcnow


def main() -> None:
    bootstrap_env()
    settings = get_settings()
    setup_logging(settings.log_level)
    log = logging.getLogger(__name__)

    if settings.app_mode != "calendar_sync":
        raise RuntimeError(
            f"calendar_sync_main launched with APP_MODE={settings.app_mode!r}"
        )
    if not settings.event_calendar_watchlist_bucket:
        raise RuntimeError("EVENT_CALENDAR_WATCHLIST_BUCKET must be set")
    if not settings.event_calendar_registry_bucket:
        raise RuntimeError("EVENT_CALENDAR_REGISTRY_BUCKET must be set")

    log.info("startup", extra={"safe_settings": settings.safe_dict()})

    gcs = GCSService(
        settings.gcs_project_id, settings.gcs_custom_storage_endpoint
    )
    claude = ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_model_max_tokens,
        base_url=settings.anthropic_api_base_url,
        timeout_seconds=settings.anthropic_request_timeout_seconds,
        max_retries=settings.anthropic_max_retries,
        retry_base_delay=settings.anthropic_retry_base_delay_seconds,
    )
    scraper = EarningsCalendarScraper(
        claude, settings.anthropic_web_search_max_uses
    )
    registry = TaskRegistry(
        gcs,
        settings.event_calendar_registry_bucket,
        settings.event_calendar_registry_prefix,
    )

    inserted = run_sync(
        gcs=gcs,
        scraper=scraper,
        registry=registry,
        watchlist_bucket=settings.event_calendar_watchlist_bucket,
        watchlist_blob=settings.event_calendar_watchlist_blob,
        lookahead_days=settings.event_calendar_lookahead_days,
        pre_earnings_offset=settings.event_calendar_pre_earnings_offset_minutes,
        ects_offset=settings.event_calendar_ects_offset_minutes,
    )
    log.info("calendar_sync_complete", extra={"new_tasks": inserted})


def run_sync(
    *,
    gcs,
    scraper: EarningsCalendarScraper,
    registry: TaskRegistry,
    watchlist_bucket: str,
    watchlist_blob: str,
    lookahead_days: int,
    pre_earnings_offset: int,
    ects_offset: int,
) -> int:
    """Pure orchestration — extracted for unit testing."""
    log = logging.getLogger(__name__)

    raw = gcs.read_json(watchlist_bucket, watchlist_blob)
    entries = [WatchlistEntry(**e) for e in raw]

    horizon = utcnow() + timedelta(days=lookahead_days)

    new_tasks: list[ScheduledTask] = []
    for entry in entries:
        event = scraper.fetch(entry)
        if event is None:
            continue
        if event.earnings_call_time > horizon:
            log.info(
                "earnings_event_beyond_horizon",
                extra={
                    "ticker": entry.ticker,
                    "earnings_call_time": event.earnings_call_time.isoformat(),
                    "horizon": horizon.isoformat(),
                },
            )
            continue

        call_time = event.earnings_call_time
        pre_exec = call_time + timedelta(minutes=pre_earnings_offset)
        ects_exec = call_time + timedelta(minutes=ects_offset)

        new_tasks.append(
            ScheduledTask(
                task_id=ScheduledTask.make_id(
                    entry.ticker,
                    entry.fiscal_quarter,
                    entry.fiscal_year,
                    "pre_earnings",
                ),
                event_type="pre_earnings",
                ticker=entry.ticker,
                fiscal_year=entry.fiscal_year,
                fiscal_quarter=entry.fiscal_quarter,
                event_time_iso=call_time.isoformat(),
                execution_time_iso=pre_exec.isoformat(),
            )
        )
        new_tasks.append(
            ScheduledTask(
                task_id=ScheduledTask.make_id(
                    entry.ticker,
                    entry.fiscal_quarter,
                    entry.fiscal_year,
                    "ects",
                ),
                event_type="ects",
                ticker=entry.ticker,
                fiscal_year=entry.fiscal_year,
                fiscal_quarter=entry.fiscal_quarter,
                event_time_iso=call_time.isoformat(),
                execution_time_iso=ects_exec.isoformat(),
            )
        )

    return registry.upsert(new_tasks)


if __name__ == "__main__":
    main()
