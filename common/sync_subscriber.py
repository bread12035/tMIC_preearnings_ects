"""
common/sync_subscriber.py

Synchronous Pub/Sub subscriber. Replaces common/pubsub.py.
No asyncio, no queue bridge, no consumer tasks.

Design:
  - Pub/Sub's internal thread pool calls _callback directly.
  - _callback runs the handler synchronously (blocking the thread).
  - FlowControl(max_messages=N) ensures at most N messages are in-flight per Pod.
  - Ack/nack is done inside _callback after handler returns.
  - Main thread blocks on streaming_future.result().
  - ContextVar isolation: handlers MUST set their own ctx_* values at the top
    of their entry function. Pub/Sub SDK's thread pool reuses threads, so a
    previous message's context can leak across calls.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from google.cloud import pubsub_v1

log = logging.getLogger(__name__)

# Returns: True -> ack, False -> nack
# Raises  -> nack (logged at WARNING)
SyncMessageHandler = Callable[[dict, dict], bool]


class SyncSubscriber:
    """
    Thin wrapper around pubsub_v1.SubscriberClient for synchronous use.

    Lifecycle:
      1. start()   - registers callback, begins StreamingPull
      2. run()     - blocks main thread until shutdown() is called from another thread
      3. shutdown()- cancels stream, waits for in-flight messages to finish
    """

    def __init__(
        self,
        project_id: str,
        subscription: str,
        handler: SyncMessageHandler,
        max_messages: int = 5,
        max_lease_duration: int = 3600,
    ):
        self._project_id = project_id
        self._subscription = subscription
        self._handler = handler
        self._max_messages = max_messages
        self._max_lease_duration = max_lease_duration
        self._client: pubsub_v1.SubscriberClient | None = None
        self._streaming_future = None

    def start(self) -> None:
        self._client = pubsub_v1.SubscriberClient()
        sub_path = self._client.subscription_path(
            self._project_id, self._subscription
        )

        def _callback(message) -> None:
            attrs = dict(message.attributes)
            try:
                payload = json.loads(message.data.decode("utf-8"))
            except Exception:
                log.error(
                    "pubsub_decode_error",
                    extra={"data_len": len(message.data)},
                )
                message.ack()  # poison pill: drop it
                return

            try:
                ok = self._handler(payload, attrs)
                if ok:
                    message.ack()
                else:
                    message.nack()
            except Exception:
                log.warning(
                    "pubsub_handler_unexpected_error", exc_info=True
                )
                message.nack()

        flow = pubsub_v1.types.FlowControl(
            max_messages=self._max_messages,
            max_lease_duration=self._max_lease_duration,
        )
        self._streaming_future = self._client.subscribe(
            sub_path, callback=_callback, flow_control=flow
        )
        log.info(
            "sync_subscriber_started",
            extra={
                "subscription": self._subscription,
                "max_messages": self._max_messages,
                "max_lease_duration": self._max_lease_duration,
            },
        )

    def run(self) -> None:
        """Block main thread. Returns when shutdown() cancels the stream."""
        assert self._streaming_future is not None
        try:
            self._streaming_future.result()
        except Exception:
            # Cancelled by shutdown() or a fatal stream error.
            pass

    def shutdown(self) -> None:
        log.info("sync_subscriber_shutdown_start")
        if self._streaming_future is not None:
            self._streaming_future.cancel()
            try:
                self._streaming_future.result(timeout=30)
            except Exception:
                pass
        if self._client is not None:
            self._client.close()
        log.info("sync_subscriber_shutdown_complete")
