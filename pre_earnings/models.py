"""Pydantic models for pre-earnings workflow."""

from __future__ import annotations

from pydantic import BaseModel


class PreEarningsMessage(BaseModel):
    """Decoded from Pub/Sub message data."""

    ticker: str
    fiscal_year: str
    fiscal_quarter: str
    event_time_iso: str  # ISO8601, when the earnings call starts
