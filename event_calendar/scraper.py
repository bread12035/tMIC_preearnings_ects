"""Resolve earnings call times for watchlist entries.

Priority chain:
  1. override_call_time set in watchlist  -> use directly (source="manual")
  2. yfinance.Ticker(ticker).calendar     -> if call time has hour-level precision
  3. Claude web_search fallback           -> parse press release / IR calendar
  4. None                                 -> log warning, caller skips ticker
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from common.claude_client import ClaudeClient, web_search_tool
from event_calendar.models import EarningsEvent, WatchlistEntry

log = logging.getLogger(__name__)


_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\"earnings_call_time\"[^{}]*\}", re.DOTALL)


class EarningsCalendarScraper:
    def __init__(self, claude_client: ClaudeClient, web_search_max_uses: int):
        self._claude = claude_client
        self._web_search_max_uses = web_search_max_uses

    async def fetch(self, entry: WatchlistEntry) -> EarningsEvent | None:
        # 1. manual override
        if entry.override_call_time:
            dt = _parse_iso_utc(entry.override_call_time)
            return EarningsEvent(
                ticker=entry.ticker,
                fiscal_year=entry.fiscal_year,
                fiscal_quarter=entry.fiscal_quarter,
                earnings_call_time=dt,
                source="manual",
            )

        # 2. yfinance
        event = self._from_yfinance(entry)
        if event and self._has_precise_time(event):
            return event

        # 3. web_search fallback
        event = await self._from_web_search(entry)
        if event:
            return event

        log.warning(
            "earnings_time_not_found",
            extra={
                "ticker": entry.ticker,
                "fiscal_year": entry.fiscal_year,
                "fiscal_quarter": entry.fiscal_quarter,
            },
        )
        return None

    @staticmethod
    def _has_precise_time(event: EarningsEvent) -> bool:
        # yfinance often returns midnight UTC (date-only precision); treat as unknown
        t = event.earnings_call_time
        return not (t.hour == 0 and t.minute == 0)

    def _from_yfinance(self, entry: WatchlistEntry) -> EarningsEvent | None:
        try:
            import yfinance as yf  # imported lazily so unit tests can stub
        except Exception:
            log.warning("yfinance_unavailable", extra={"ticker": entry.ticker})
            return None
        try:
            cal = yf.Ticker(entry.ticker).calendar
            raw = cal["Earnings Date"][0] if isinstance(cal, dict) else cal.iloc[0]["Earnings Date"]
            if isinstance(raw, datetime):
                dt = raw
            else:
                dt = datetime.fromisoformat(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return EarningsEvent(
                ticker=entry.ticker,
                fiscal_year=entry.fiscal_year,
                fiscal_quarter=entry.fiscal_quarter,
                earnings_call_time=dt,
                source="yfinance",
            )
        except Exception:
            log.warning(
                "yfinance_lookup_failed",
                extra={"ticker": entry.ticker},
                exc_info=True,
            )
            return None

    async def _from_web_search(
        self, entry: WatchlistEntry
    ) -> EarningsEvent | None:
        system = (
            "You are a financial data assistant. Search for the official "
            "earnings call date and time."
        )
        user = (
            f"Find the exact earnings call date and time (UTC) for "
            f"{entry.company_name} ({entry.ticker}) for fiscal "
            f"{entry.fiscal_quarter} {entry.fiscal_year}. "
            f'Return ONLY a JSON object: '
            f'{{"earnings_call_time": "<ISO8601 UTC>"}}'
        )
        try:
            result = await self._claude.complete(
                system=system,
                user_prompt=user,
                tools=[web_search_tool(self._web_search_max_uses)],
            )
            data = _extract_json_object(result)
            dt = _parse_iso_utc(data["earnings_call_time"])
            return EarningsEvent(
                ticker=entry.ticker,
                fiscal_year=entry.fiscal_year,
                fiscal_quarter=entry.fiscal_quarter,
                earnings_call_time=dt,
                source="web_search",
            )
        except Exception:
            log.warning(
                "web_search_lookup_failed",
                extra={"ticker": entry.ticker},
                exc_info=True,
            )
            return None


def _parse_iso_utc(s: str) -> datetime:
    """Parse an ISO8601 UTC string and return a naive UTC datetime."""
    normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _extract_json_object(text: str) -> dict:
    """Find the first {...} block containing 'earnings_call_time' and parse it."""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError(f"no JSON object with earnings_call_time in: {text!r}")
    return json.loads(match.group(0))
