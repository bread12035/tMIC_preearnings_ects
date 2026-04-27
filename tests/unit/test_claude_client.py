"""Tests for common.claude_client.ClaudeClient retry behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from anthropic import APIStatusError, RateLimitError

from common.claude_client import ClaudeClient, web_search_tool
from common.exceptions import (
    ClaudeAPIError,
    ClaudeAPIRetryExhaustedError,
)


def _make_client(max_retries: int = 3) -> ClaudeClient:
    # Avoid real network init by using a placeholder API key (AsyncAnthropic
    # accepts the value at construction; nothing is dispatched until call time).
    return ClaudeClient(
        api_key="sk-ant-test",
        model="claude-test",
        max_tokens=100,
        base_url="https://api.anthropic.com",
        timeout_seconds=5,
        max_retries=max_retries,
        retry_base_delay=0,  # zero delay so tests run fast
    )


def _ok_response(text: str) -> SimpleNamespace:
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block])


@pytest.mark.asyncio
async def test_complete_returns_text_on_success() -> None:
    client = _make_client()
    client._client = MagicMock()
    client._client.messages.create = AsyncMock(return_value=_ok_response("hello"))

    out = await client.complete(system="sys", user_prompt="user")
    assert out == "hello"


@pytest.mark.asyncio
async def test_4xx_does_not_retry() -> None:
    client = _make_client(max_retries=3)
    client._client = MagicMock()

    fake_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake_response = httpx.Response(400, request=fake_request)
    client._client.messages.create = AsyncMock(
        side_effect=APIStatusError("bad request", response=fake_response, body=None)
    )

    with pytest.raises(ClaudeAPIError):
        await client.complete(system="s", user_prompt="u")
    assert client._client.messages.create.call_count == 1


@pytest.mark.asyncio
async def test_rate_limit_exhausts_after_max_retries() -> None:
    client = _make_client(max_retries=3)
    client._client = MagicMock()

    fake_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake_response = httpx.Response(429, request=fake_request)
    client._client.messages.create = AsyncMock(
        side_effect=RateLimitError(
            "rate limited", response=fake_response, body=None
        )
    )

    with patch("common.claude_client.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ClaudeAPIRetryExhaustedError):
            await client.complete(system="s", user_prompt="u")
    assert client._client.messages.create.call_count == 3


@pytest.mark.asyncio
async def test_5xx_retried_then_succeeds() -> None:
    client = _make_client(max_retries=3)
    client._client = MagicMock()

    fake_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake_response = httpx.Response(500, request=fake_request)

    client._client.messages.create = AsyncMock(
        side_effect=[
            APIStatusError("server error", response=fake_response, body=None),
            _ok_response("recovered"),
        ]
    )

    with patch("common.claude_client.asyncio.sleep", new=AsyncMock()):
        out = await client.complete(system="s", user_prompt="u")
    assert out == "recovered"
    assert client._client.messages.create.call_count == 2


def test_web_search_tool_definition() -> None:
    t = web_search_tool(7)
    assert t["type"] == "web_search_20250305"
    assert t["name"] == "web_search"
    assert t["max_uses"] == 7
