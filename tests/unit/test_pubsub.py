"""Tests for common.pubsub.AsyncSubscriber.

We can't run a real Pub/Sub thread pool in unit tests, so we exercise the
async-side: enqueue messages directly via the asyncio Queue and verify
ack/nack behavior. The thread bridge is covered in integration tests.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from common.pubsub import AsyncSubscriber


def _fake_message(payload: dict, attrs: dict | None = None) -> MagicMock:
    m = MagicMock()
    m.data = json.dumps(payload).encode("utf-8")
    m.attributes = attrs or {}
    return m


@pytest.mark.asyncio
async def test_handler_true_acks() -> None:
    async def handler(payload, attrs):
        return True

    sub = AsyncSubscriber("p", "s", handler, max_inflight=1)
    sub._queue = asyncio.Queue(maxsize=1)
    sub._loop = asyncio.get_running_loop()

    msg = _fake_message({"hello": "world"})
    await sub._queue.put(msg)

    consumer = asyncio.create_task(sub._consume_loop(0))
    await asyncio.sleep(0.05)
    sub._shutdown_event.set()
    await consumer

    msg.ack.assert_called_once()
    msg.nack.assert_not_called()


@pytest.mark.asyncio
async def test_handler_false_nacks() -> None:
    async def handler(payload, attrs):
        return False

    sub = AsyncSubscriber("p", "s", handler, max_inflight=1)
    sub._queue = asyncio.Queue(maxsize=1)
    sub._loop = asyncio.get_running_loop()

    msg = _fake_message({"k": 1})
    await sub._queue.put(msg)

    consumer = asyncio.create_task(sub._consume_loop(0))
    await asyncio.sleep(0.05)
    sub._shutdown_event.set()
    await consumer

    msg.nack.assert_called_once()
    msg.ack.assert_not_called()


@pytest.mark.asyncio
async def test_handler_exception_nacks() -> None:
    async def handler(payload, attrs):
        raise RuntimeError("boom")

    sub = AsyncSubscriber("p", "s", handler, max_inflight=1)
    sub._queue = asyncio.Queue(maxsize=1)
    sub._loop = asyncio.get_running_loop()

    msg = _fake_message({"k": 1})
    await sub._queue.put(msg)

    consumer = asyncio.create_task(sub._consume_loop(0))
    await asyncio.sleep(0.05)
    sub._shutdown_event.set()
    await consumer

    msg.nack.assert_called_once()


@pytest.mark.asyncio
async def test_bad_payload_acks_to_drop() -> None:
    handler_called = []

    async def handler(payload, attrs):
        handler_called.append((payload, attrs))
        return True

    sub = AsyncSubscriber("p", "s", handler, max_inflight=1)
    sub._queue = asyncio.Queue(maxsize=1)
    sub._loop = asyncio.get_running_loop()

    msg = MagicMock()
    msg.data = b"not-json"
    msg.attributes = {}
    await sub._queue.put(msg)

    consumer = asyncio.create_task(sub._consume_loop(0))
    await asyncio.sleep(0.05)
    sub._shutdown_event.set()
    await consumer

    msg.ack.assert_called_once()
    assert handler_called == []  # handler never invoked on bad payload


@pytest.mark.asyncio
async def test_enqueue_or_nack_nacks_on_full_queue() -> None:
    async def handler(payload, attrs):
        return True

    sub = AsyncSubscriber("p", "s", handler, max_inflight=1)
    sub._queue = asyncio.Queue(maxsize=1)
    sub._loop = asyncio.get_running_loop()

    # fill the queue
    blocker = MagicMock()
    blocker.data = b'{}'
    blocker.attributes = {}
    sub._queue.put_nowait(blocker)

    overflow = MagicMock()
    overflow.data = b'{}'
    overflow.attributes = {}

    sub._enqueue_or_nack(overflow)
    overflow.nack.assert_called_once()
