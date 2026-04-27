"""Async Pub/Sub subscriber bridge.

Bridges Pub/Sub's threaded callback model to asyncio. The subscribe() callback
runs in a Google client thread; we hand each message to an asyncio.Queue via
loop.call_soon_threadsafe and run consumer tasks on the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.subscriber.message import Message

log = logging.getLogger(__name__)


# Returns: True -> ack, False -> nack
# Raises -> nack (logged at warning level)
MessageHandler = Callable[[dict, dict], Awaitable[bool]]


class AsyncSubscriber:
    """
    Bridges Pub/Sub's threaded callback model to asyncio.

    Lifecycle:
      1. start()          - launches background StreamingPull
      2. process forever  - consumer tasks pull from internal queue
      3. shutdown()       - graceful drain on SIGTERM
    """

    def __init__(
        self,
        project_id: str,
        subscription: str,
        handler: MessageHandler,
        max_inflight: int = 20,
    ):
        self._project_id = project_id
        self._subscription = subscription
        self._handler = handler
        self._max_inflight = max_inflight

        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._streaming_future = None
        self._consumer_tasks: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()
        self._client: pubsub_v1.SubscriberClient | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._max_inflight)
        self._client = pubsub_v1.SubscriberClient()

        sub_path = self._client.subscription_path(
            self._project_id, self._subscription
        )

        # Pub/Sub callback runs in client's internal thread pool
        def _callback(message: Message) -> None:
            try:
                # Hand the message to the asyncio loop
                self._loop.call_soon_threadsafe(  # type: ignore[union-attr]
                    self._enqueue_or_nack, message
                )
            except Exception:
                # If the loop is gone, nack so Pub/Sub redelivers
                try:
                    message.nack()
                except Exception:
                    pass

        flow = pubsub_v1.types.FlowControl(max_messages=self._max_inflight)
        self._streaming_future = self._client.subscribe(
            sub_path, callback=_callback, flow_control=flow
        )

        # Spawn consumer tasks
        for i in range(self._max_inflight):
            task = asyncio.create_task(self._consume_loop(worker_id=i))
            self._consumer_tasks.append(task)

        log.info(
            "pubsub_subscriber_started",
            extra={
                "subscription": self._subscription,
                "max_inflight": self._max_inflight,
            },
        )

    def _enqueue_or_nack(self, message: Message) -> None:
        """Runs on the event loop thread (via call_soon_threadsafe)."""
        try:
            self._queue.put_nowait(message)  # type: ignore[union-attr]
        except asyncio.QueueFull:
            # Backpressure: nack so Pub/Sub redelivers later
            message.nack()

    async def _consume_loop(self, worker_id: int) -> None:
        """Pull from queue, run handler, ack/nack based on outcome."""
        assert self._queue is not None
        while not self._shutdown_event.is_set():
            try:
                message: Message = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            attrs = dict(message.attributes)
            try:
                payload = self._decode_payload(message.data)
            except Exception:
                log.error(
                    "pubsub_decode_error",
                    extra={"worker_id": worker_id, "data_len": len(message.data)},
                )
                # Bad message format: ack to drop (avoid poison pill)
                message.ack()
                continue

            try:
                ok = await self._handler(payload, attrs)
                if ok:
                    message.ack()
                else:
                    message.nack()
            except Exception:
                log.warning(
                    "pubsub_handler_unexpected_error",
                    extra={"worker_id": worker_id},
                    exc_info=True,
                )
                # Handler bug: nack so it can be retried
                # (subject to delivery attempts limit on subscription)
                message.nack()

    @staticmethod
    def _decode_payload(data: bytes) -> dict:
        return json.loads(data.decode("utf-8"))

    async def run_forever(self) -> None:
        """Block until shutdown_event is set."""
        await self._shutdown_event.wait()

    async def shutdown(self, drain_timeout: float = 30.0) -> None:
        log.info("pubsub_subscriber_shutdown_start")
        # Stop pulling new messages
        if self._streaming_future is not None:
            self._streaming_future.cancel()
            try:
                self._streaming_future.result(timeout=10)
            except Exception:
                pass

        # Signal consumers
        self._shutdown_event.set()

        # Wait for consumers to drain
        if self._consumer_tasks:
            try:
                await asyncio.wait(self._consumer_tasks, timeout=drain_timeout)
            except Exception:
                pass

        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass

        log.info("pubsub_subscriber_shutdown_complete")
