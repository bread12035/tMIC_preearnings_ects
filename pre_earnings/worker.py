"""Pub/Sub message handler for pre-earnings.

Critical: ack-on-receive. Polling runs as a background task spawned from the
handler; the handler returns True (ack) immediately so Pub/Sub doesn't
redeliver while we're still polling.
"""

from __future__ import annotations

import asyncio
import logging

from common.logging import ctx_message_id, ctx_ticker, ctx_workflow
from pre_earnings.models import PreEarningsMessage
from pre_earnings.monitor import PreEarningsMonitor

log = logging.getLogger(__name__)


class PreEarningsWorker:
    def __init__(self, monitor: PreEarningsMonitor):
        self._monitor = monitor
        self._background_tasks: set[asyncio.Task] = set()

    async def handle(self, payload: dict, attrs: dict) -> bool:
        """
        Pub/Sub message handler.
        Returns True immediately (ack) and spawns background polling task.
        """
        ctx_workflow.set("pre_earnings")
        ctx_message_id.set(attrs.get("message_id", "?"))

        try:
            msg = PreEarningsMessage(**payload)
        except Exception:
            log.error("malformed_message", extra={"payload": payload})
            # Malformed message: ack to drop
            return True

        ctx_ticker.set(msg.ticker)

        # Fire-and-forget: polling continues after this returns
        task = asyncio.create_task(self._run_monitor(msg))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return True  # ack immediately

    async def _run_monitor(self, msg: PreEarningsMessage) -> None:
        # Make context vars available inside the background task so logs are tagged.
        ctx_workflow.set("pre_earnings")
        ctx_ticker.set(msg.ticker)
        try:
            await self._monitor.run(msg)
        except Exception:
            log.error(
                "monitor_unexpected_error",
                extra={"ticker": msg.ticker},
                exc_info=True,
            )
