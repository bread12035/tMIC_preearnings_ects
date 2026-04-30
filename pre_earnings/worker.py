"""Pub/Sub message handler for pre-earnings (sync).

New design: block in monitor.run() for the full polling window, then
ack (success / exhausted) or nack (unexpected error). The message is
held (not acked) until the polling loop completes.
"""

from __future__ import annotations

import logging

from common.logging import ctx_message_id, ctx_ticker, ctx_workflow
from pre_earnings.models import PreEarningsMessage
from pre_earnings.monitor import PreEarningsMonitor

log = logging.getLogger(__name__)


class PreEarningsWorker:
    def __init__(self, monitor: PreEarningsMonitor):
        self._monitor = monitor

    def handle(self, payload: dict, attrs: dict) -> bool:
        # Reset ContextVars first — Pub/Sub SDK reuses threads, so values
        # from a previous message on this thread may still be set.
        ctx_workflow.set("pre_earnings")
        ctx_message_id.set(attrs.get("message_id", "?"))
        ctx_ticker.set("?")

        try:
            msg = PreEarningsMessage(**payload)
        except Exception:
            log.error("malformed_message", extra={"payload": payload})
            return True  # ack to drop poison pill

        ctx_ticker.set(msg.ticker)
        log.info("pre_earnings_start")

        try:
            self._monitor.run(msg)
            return True  # ack: success or exhausted (monitor handles audit)
        except Exception:
            log.error("pre_earnings_unexpected_error", exc_info=True)
            return False  # nack: unexpected crash, retry later
