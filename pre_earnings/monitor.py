"""Pre-earnings polling loop (sync).

Blocks the calling thread for the full polling window. The Pub/Sub SDK's
lease management automatically extends the ack deadline while this runs
(controlled by FlowControl.max_lease_duration — see SDD §3.2).
"""

from __future__ import annotations

import logging
import time
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
        stocktitan_news_url: str,
        prompt_system_path: str,
        prompt_user_path: str,
    ):
        self._gcs = gcs
        self._claude = claude
        self._config_loader = config_loader
        self._output_bucket = output_bucket
        self._output_prefix = output_prefix
        self._web_search_max_uses = web_search_max_uses
        self._stocktitan_news_url = stocktitan_news_url
        self._prompt_system_path = prompt_system_path
        self._prompt_user_path = prompt_user_path

    def run(self, msg: PreEarningsMessage) -> None:
        """
        Blocking polling loop. Returns when press release is found,
        or after max_attempts. Caller acks in both cases.
        """
        try:
            cfg = self._config_loader.load_pre_earnings(msg.ticker)
        except CompanyConfigNotFoundError as e:
            log.error("config_not_found", extra={"error": str(e)})
            return
        except CompanyConfigInvalidError as e:
            log.error("config_invalid", extra={"error": str(e)})
            return

        self._wait_until_start(msg, cfg)

        for attempt in range(cfg.polling.max_attempts):
            log.info(
                "polling_attempt",
                extra={"attempt": attempt + 1, "max": cfg.polling.max_attempts},
            )
            # Touch /tmp/alive for K8s liveness probe
            try:
                open("/tmp/alive", "w").close()
            except OSError:
                pass

            try:
                summary = self._try_fetch_and_summarize(msg, cfg)
                output_path = self._build_output_path(msg)
                self._gcs.write_text(self._output_bucket, output_path, summary)
                log.info(
                    "press_release_captured",
                    extra={"attempt": attempt + 1, "path": output_path},
                )
                return
            except PressReleaseNotFoundError:
                log.info("press_release_not_yet")
            except ClaudeAPIRetryExhaustedError as e:
                # LLM service down; treat as soft fail, continue polling
                log.warning("claude_down_continuing", extra={"error": str(e)})

            if attempt < cfg.polling.max_attempts - 1:
                time.sleep(cfg.polling.interval_minutes * 60)

        log.warning(
            "polling_exhausted", extra={"attempts": cfg.polling.max_attempts}
        )
        self._write_audit(msg, "polling_exhausted")

    def _wait_until_start(
        self, msg: PreEarningsMessage, cfg: PreEarningsCompanyConfig
    ) -> None:
        """Sleep until polling start time.

        The Pub/Sub SDK's lease management automatically extends the ack
        deadline during this sleep, as long as FlowControl.max_lease_duration
        is set high enough (see SDD §3.2).
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
            time.sleep(delay)

    def _try_fetch_and_summarize(
        self, msg: PreEarningsMessage, cfg: PreEarningsCompanyConfig
    ) -> str:
        system, user = build_pre_earnings_prompt(
            msg,
            cfg,
            stocktitan_news_url=self._stocktitan_news_url,
            system_template_path=self._prompt_system_path,
            user_template_path=self._prompt_user_path,
        )
        result = self._claude.complete(
            system=system,
            user_prompt=user,
            tools=[web_search_tool(self._web_search_max_uses)],
        )

        # Sentinel: if Claude couldn't find a release, the prompt instructs it
        # to return exactly this string.
        if "PRESS_RELEASE_NOT_AVAILABLE" in result:
            raise PressReleaseNotFoundError()
        return result

    def _build_output_path(self, msg: PreEarningsMessage) -> str:
        return (
            f"{self._output_prefix}/company={msg.ticker}/"
            f"quarter={msg.fiscal_quarter}/fiscal={msg.fiscal_year}/"
            f"{msg.ticker}_FY_{msg.fiscal_quarter}_{msg.fiscal_year}.md"
        )

    def _write_audit(self, msg: PreEarningsMessage, reason: str) -> None:
        audit_path = (
            f"{self._output_prefix}/company={msg.ticker}/"
            f"quarter={msg.fiscal_quarter}/fiscal={msg.fiscal_year}/audit.json"
        )
        self._gcs.write_json(
            self._output_bucket,
            audit_path,
            {"ticker": msg.ticker, "reason": reason},
        )
