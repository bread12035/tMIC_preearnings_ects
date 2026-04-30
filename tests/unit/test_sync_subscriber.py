"""Tests for common.sync_subscriber.SyncSubscriber.

We exercise the callback logic directly — inject fake messages and verify
ack/nack behavior without needing a real Pub/Sub connection.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from common.sync_subscriber import SyncSubscriber


def _fake_message(payload: dict, attrs: dict | None = None) -> MagicMock:
    m = MagicMock()
    m.data = json.dumps(payload).encode("utf-8")
    m.attributes = attrs or {}
    return m


def _get_callback(subscriber: SyncSubscriber) -> callable:
    """Extract the _callback closure by calling start() with a stubbed client."""
    captured = {}

    fake_future = MagicMock()
    fake_future.done.return_value = False

    fake_client = MagicMock()
    fake_client.subscription_path.return_value = "projects/p/subscriptions/s"

    def fake_subscribe(sub_path, callback, flow_control):
        captured["callback"] = callback
        return fake_future

    fake_client.subscribe = fake_subscribe
    subscriber._client = fake_client

    import pubsub_v1_stub  # noqa: F401 — replaced below by monkeypatch

    # Patch pubsub_v1.SubscriberClient to return our fake_client
    import unittest.mock as mock
    with mock.patch("common.sync_subscriber.pubsub_v1.SubscriberClient", return_value=fake_client):
        subscriber.start()

    return captured["callback"]


def _build_subscriber(handler) -> tuple[SyncSubscriber, callable]:
    """Build a SyncSubscriber and extract its internal _callback."""
    sub = SyncSubscriber("proj", "sub", handler, max_messages=5)

    captured: dict = {}
    fake_future = MagicMock()
    fake_client = MagicMock()
    fake_client.subscription_path.return_value = "projects/proj/subscriptions/sub"

    def fake_subscribe(sub_path, callback, flow_control):
        captured["callback"] = callback
        return fake_future

    fake_client.subscribe = fake_subscribe

    import unittest.mock as mock
    with mock.patch("common.sync_subscriber.pubsub_v1.SubscriberClient", return_value=fake_client):
        sub.start()

    return sub, captured["callback"]


def test_handler_true_acks() -> None:
    def handler(payload, attrs):
        return True

    _, callback = _build_subscriber(handler)
    msg = _fake_message({"hello": "world"})
    callback(msg)

    msg.ack.assert_called_once()
    msg.nack.assert_not_called()


def test_handler_false_nacks() -> None:
    def handler(payload, attrs):
        return False

    _, callback = _build_subscriber(handler)
    msg = _fake_message({"k": 1})
    callback(msg)

    msg.nack.assert_called_once()
    msg.ack.assert_not_called()


def test_handler_exception_nacks() -> None:
    def handler(payload, attrs):
        raise RuntimeError("boom")

    _, callback = _build_subscriber(handler)
    msg = _fake_message({"k": 1})
    callback(msg)

    msg.nack.assert_called_once()


def test_bad_payload_acks_to_drop() -> None:
    handler_called = []

    def handler(payload, attrs):
        handler_called.append((payload, attrs))
        return True

    _, callback = _build_subscriber(handler)

    msg = MagicMock()
    msg.data = b"not-json"
    msg.attributes = {}
    callback(msg)

    msg.ack.assert_called_once()
    assert handler_called == []  # handler never invoked on bad payload


def test_attrs_passed_to_handler() -> None:
    received: dict = {}

    def handler(payload, attrs):
        received.update(attrs)
        return True

    _, callback = _build_subscriber(handler)
    msg = _fake_message({"x": 1}, attrs={"event_type": "pre_earnings"})
    callback(msg)

    assert received == {"event_type": "pre_earnings"}
