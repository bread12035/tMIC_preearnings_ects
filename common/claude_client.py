"""Sync Claude wrapper with exponential-backoff retry."""

from __future__ import annotations

import logging
import time

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    Anthropic,
    RateLimitError,
)

from common.exceptions import (
    ClaudeAPIError,
    ClaudeAPIRateLimitError,
    ClaudeAPIRetryExhaustedError,
    ClaudeAPITimeoutError,
)

log = logging.getLogger(__name__)


class ClaudeClient:
    """
    Sync Claude wrapper with exponential backoff retry.
    All public methods raise ClaudeAPIRetryExhaustedError after max_retries.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        base_url: str,
        timeout_seconds: int,
        max_retries: int = 5,
        retry_base_delay: int = 2,
    ):
        self._client = Anthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._model = model
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay

    def complete(
        self,
        system: str,
        user_prompt: str,
        tools: list[dict] | None = None,
    ) -> str:
        """
        Returns concatenated text from response.
        For pre-earnings, pass tools=[web_search_tool_definition].
        """
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                kwargs: dict = {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
                if tools:
                    kwargs["tools"] = tools

                resp = self._client.messages.create(**kwargs)
                # Extract text blocks (skip tool_use blocks)
                texts = [
                    block.text
                    for block in resp.content
                    if getattr(block, "type", None) == "text"
                ]
                return "\n".join(texts)

            except RateLimitError as e:
                last_exc = ClaudeAPIRateLimitError(str(e))
                log.warning("claude_rate_limited", extra={"attempt": attempt + 1})
            except (APIConnectionError, APITimeoutError) as e:
                last_exc = ClaudeAPITimeoutError(str(e))
                log.warning("claude_connection_error", extra={"attempt": attempt + 1})
            except APIStatusError as e:
                if e.status_code >= 500:
                    last_exc = ClaudeAPIError(f"5xx: {e}")
                    log.warning(
                        "claude_5xx",
                        extra={"attempt": attempt + 1, "status": e.status_code},
                    )
                else:
                    # 4xx -> permanent, don't retry
                    raise ClaudeAPIError(f"4xx: {e}") from e
            except Exception as e:
                last_exc = ClaudeAPIError(f"Unexpected: {e}")
                log.warning(
                    "claude_unexpected_error",
                    extra={"attempt": attempt + 1},
                    exc_info=True,
                )

            if attempt < self._max_retries - 1:
                delay = self._retry_base_delay * (2 ** attempt)
                log.warning(
                    "claude_retry",
                    extra={"attempt": attempt + 1, "delay": delay},
                )
                time.sleep(delay)

        raise ClaudeAPIRetryExhaustedError(
            f"Claude API failed after {self._max_retries} attempts: {last_exc}"
        ) from last_exc


def web_search_tool(max_uses: int) -> dict:
    """Tool definition for pre-earnings Claude calls."""
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max_uses,
    }
