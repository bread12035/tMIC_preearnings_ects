"""Pre-earnings polling loop.

Runs entirely in memory; if the Pod restarts, in-flight polls are lost
(see SDD section 11 for the future Firestore-checkpoint plan).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from common.claude_client import ClaudeClient, web_search_tool
from common.company_config import (
    CompanyConfigLoader,
    PreEarningsCompanyConfig,
)
from common.exceptions import (
    ClaudeAPIRetryExhaustedError,
    CompanyConfigInvalidError,
    CompanyConfigNotFoundError,
    PressReleaseNotFoundError,
)
from common.gcs_service import GCSService
from pre_earnings.models import PreEarningsMessage
from pre_earnings.prompt_builder import build_pre_earnings_prompt

log = logging.getLogger(__name__)


class PreEarningsMonitor:
    def __init__(
        self,
        gcs: GCSService,
        claude: ClaudeClient,
        config_loader: CompanyConfigLoader,
        output_bucket: str,
        output_prefix: str,
        web_search_max_uses: int,
    ):
        self._gcs = gcs
        self._claude = claude
        self._config_loader = config_loader
        self._output_bucket = output_bucket
        self._output_prefix = output_prefix
        self._web_search_max_uses = web_search_max_uses

    async def run(self, msg: PreEarningsMessage) -> None:
        try:
            cfg = await self._config_loader.load_pre_earnings(msg.ticker)
        except CompanyConfigNotFoundError as e:
            log.error("config_not_found", extra={"error": str(e)})
            return
        except CompanyConfigInvalidError as e:
            log.error("config_invalid", extra={"error": str(e)})
            return

        await self._wait_until_start(msg, cfg)

        for attempt in range(cfg.polling.max_attempts):
            log.info(
                "polling_attempt",
                extra={"attempt": attempt + 1, "max": cfg.polling.max_attempts},
            )
            try:
                summary = await self._try_fetch_and_summarize(msg, cfg)
                await self._write_output(msg, summary)
                log.info(
                    "press_release_captured", extra={"attempt": attempt + 1}
                )
                return
            except PressReleaseNotFoundError:
                log.info("press_release_not_yet")
            except ClaudeAPIRetryExhaustedError as e:
                # LLM service down; treat as soft fail, continue polling
                log.warning("claude_down_continuing", extra={"error": str(e)})

            if attempt < cfg.polling.max_attempts - 1:
                await asyncio.sleep(cfg.polling.interval_minutes * 60)

        log.warning(
            "polling_exhausted", extra={"attempts": cfg.polling.max_attempts}
        )

    async def _wait_until_start(
        self, msg: PreEarningsMessage, cfg: PreEarningsCompanyConfig
    ) -> None:
        """
        If event_time is far in the future, sleep until
        (event_time - start_offset_minutes). Bounded to non-negative.
        """
        try:
            event_time = datetime.fromisoformat(
                msg.event_time_iso.replace("Z", "+00:00")
            )
        except ValueError:
            log.warning(
                "invalid_event_time", extra={"event_time": msg.event_time_iso}
            )
            return

        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)

        start_at = event_time.timestamp() - cfg.polling.start_offset_minutes * 60
        delay = start_at - datetime.now(timezone.utc).timestamp()
        if delay > 0:
            log.info("waiting_until_start", extra={"sleep_seconds": int(delay)})
            await asyncio.sleep(delay)

    async def _try_fetch_and_summarize(
        self, msg: PreEarningsMessage, cfg: PreEarningsCompanyConfig
    ) -> str:
        system, user = build_pre_earnings_prompt(msg, cfg)
        result = await self._claude.complete(
            system=system,
            user_prompt=user,
            tools=[web_search_tool(self._web_search_max_uses)],
        )

        # Sentinel: if Claude couldn't find a release, the prompt instructs it
        # to return exactly this string.
        if "PRESS_RELEASE_NOT_AVAILABLE" in result:
            raise PressReleaseNotFoundError()
        return result

    async def _write_output(
        self, msg: PreEarningsMessage, content: str
    ) -> None:
        path = (
            f"{self._output_prefix}/company={msg.ticker}/"
            f"quarter={msg.fiscal_quarter}/fiscal={msg.fiscal_year}/"
            f"{msg.ticker}_FY_{msg.fiscal_quarter}_{msg.fiscal_year}.md"
        )
        await self._gcs.write_text(self._output_bucket, path, content)
