"""Smoke tests for common.logging."""

from __future__ import annotations

import json
import logging

from common.logging import (
    JSONFormatter,
    ctx_message_id,
    ctx_ticker,
    ctx_workflow,
    setup_logging,
)


def test_json_formatter_includes_context():
    ctx_workflow.set("pre_earnings")
    ctx_ticker.set("AAPL")
    ctx_message_id.set("m1")
    fmt = JSONFormatter()

    record = logging.LogRecord(
        name="tester",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.attempt = 3

    out = fmt.format(record)
    payload = json.loads(out)
    assert payload["message"] == "hello world"
    assert payload["workflow"] == "pre_earnings"
    assert payload["ticker"] == "AAPL"
    assert payload["message_id"] == "m1"
    assert payload["level"] == "INFO"
    assert payload["attempt"] == 3


def test_setup_logging_replaces_handlers():
    setup_logging("DEBUG")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JSONFormatter)
