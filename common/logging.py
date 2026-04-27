"""Structured JSON logging with context propagation."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

# Context vars for trace propagation
ctx_ticker: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ticker", default=None
)
ctx_workflow: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workflow", default=None
)
ctx_message_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "message_id", default=None
)


_RESERVED_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "taskName",
}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "ticker": ctx_ticker.get(),
            "workflow": ctx_workflow.get(),
            "message_id": ctx_message_id.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, val in record.__dict__.items():
            if key not in _RESERVED_FIELDS and not key.startswith("_"):
                payload[key] = val
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Call once at process startup. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Remove default handlers to avoid duplicate plain-text output
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
