"""Tests for pre_earnings.worker.PreEarningsWorker (sync)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pre_earnings.worker import PreEarningsWorker


def test_handle_blocks_and_acks_on_success() -> None:
    monitor = MagicMock()
    monitor.run = MagicMock(return_value=None)

    worker = PreEarningsWorker(monitor)
    payload = {
        "ticker": "AAPL",
        "fiscal_year": "2026",
        "fiscal_quarter": "Q2",
        "event_time_iso": "2026-04-27T12:00:00+00:00",
    }

    ok = worker.handle(payload, {"message_id": "m1"})
    assert ok is True
    monitor.run.assert_called_once()


def test_handle_malformed_message_acks() -> None:
    monitor = MagicMock()
    worker = PreEarningsWorker(monitor)
    ok = worker.handle({"missing": "fields"}, {})
    assert ok is True
    monitor.run.assert_not_called()


def test_handle_nacks_on_unexpected_monitor_exception() -> None:
    monitor = MagicMock()
    monitor.run = MagicMock(side_effect=RuntimeError("monitor exploded"))
    worker = PreEarningsWorker(monitor)

    payload = {
        "ticker": "AAPL",
        "fiscal_year": "2026",
        "fiscal_quarter": "Q2",
        "event_time_iso": "2026-04-27T12:00:00+00:00",
    }

    ok = worker.handle(payload, {})
    assert ok is False  # nack: unexpected crash


def test_handle_acks_after_polling_exhaustion() -> None:
    """monitor.run() returns normally (exhausted); worker acks."""
    monitor = MagicMock()
    monitor.run = MagicMock(return_value=None)  # exhausted => returns None
    worker = PreEarningsWorker(monitor)

    payload = {
        "ticker": "MSFT",
        "fiscal_year": "2026",
        "fiscal_quarter": "Q3",
        "event_time_iso": "2026-04-27T12:00:00+00:00",
    }

    ok = worker.handle(payload, {"message_id": "m2"})
    assert ok is True
