"""Pydantic models for the event calendar registry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, field_validator


class WatchlistEntry(BaseModel):
    ticker: str
    company_name: str
    fiscal_year: str
    fiscal_quarter: str
    override_call_time: str | None = None  # ISO8601 UTC; bypasses scraper if set

    @field_validator("override_call_time")
    @classmethod
    def _require_utc(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # Accept "...Z" or explicit "+00:00"; reject anything else.
        normalized = v.replace("Z", "+00:00") if v.endswith("Z") else v
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError as e:
            raise ValueError(f"override_call_time is not valid ISO8601: {v!r}") from e
        if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(dt):
            raise ValueError(
                f"override_call_time must be UTC (tz=Z or +00:00), got {v!r}"
            )
        return v


class EarningsEvent(BaseModel):
    ticker: str
    fiscal_year: str
    fiscal_quarter: str
    earnings_call_time: datetime  # naive UTC (tzinfo stripped after conversion)
    source: Literal["yfinance", "web_search", "manual"]


class ScheduledTask(BaseModel):
    task_id: str  # "{ticker}-{fq}-{fy}-{event_type}" — dedup key
    event_type: Literal["pre_earnings", "ects"]
    ticker: str
    fiscal_year: str
    fiscal_quarter: str
    event_time_iso: str  # earnings_call_time ISO8601 (UTC)
    execution_time_iso: str  # publish-due time ISO8601 (UTC)
    status: Literal["pending", "published", "skipped"] = "pending"

    @staticmethod
    def make_id(
        ticker: str, fiscal_quarter: str, fiscal_year: str, event_type: str
    ) -> str:
        return f"{ticker}-{fiscal_quarter}-{fiscal_year}-{event_type}"
