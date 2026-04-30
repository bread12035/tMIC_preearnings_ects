"""Pub/Sub message handler for ECTS workflow (sync).

Two execution paths, selected by ``web_search_flag``:

* ``False`` (default): pull transcript + financial + segment + config from GCS,
  inline them into the prompt, and ask Claude for the summary (no web tool).
* ``True``: skip the GCS pulls; Claude uses the web_search tool to fetch the
  financial results from Stock Titan news and the transcript from Motley Fool,
  then composes the summary from the configurable inner template.
"""

from __future__ import annotations

import logging

from common.claude_client import ClaudeClient, web_search_tool
from common.exceptions import (
    ClaudeAPIRetryExhaustedError,
    DataParseError,
    GCSWriteError,
    MissingDataError,
)
from common.gcs_service import GCSService
from common.logging import ctx_message_id, ctx_ticker, ctx_workflow
from ects.data_processor import ECTSDataProcessor
from ects.models import ECTSMessage
from ects.prompt_builder import build_ects_prompt, build_ects_web_search_prompt

log = logging.getLogger(__name__)


class ECTSWorker:
    def __init__(
        self,
        processor: ECTSDataProcessor,
        claude: ClaudeClient,
        gcs: GCSService,
        output_bucket: str,
        output_prefix: str,
        *,
        web_search_flag: bool = False,
        web_search_max_uses: int = 10,
        stocktitan_news_url: str = "",
        motley_fool_url: str = "",
        prompt_system_path: str = "prompts/ects_system.md.tmpl",
        prompt_user_path: str = "prompts/ects_user.md.tmpl",
        prompt_web_search_system_path: str = "prompts/ects_web_search_system.md.tmpl",
        prompt_web_search_user_path: str = "prompts/ects_web_search_user.md.tmpl",
        prompt_web_search_template_path: str = "prompts/ects_web_search_template.md.tmpl",
    ):
        self._processor = processor
        self._claude = claude
        self._gcs = gcs
        self._output_bucket = output_bucket
        self._output_prefix = output_prefix
        self._web_search_flag = web_search_flag
        self._web_search_max_uses = web_search_max_uses
        self._stocktitan_news_url = stocktitan_news_url
        self._motley_fool_url = motley_fool_url
        self._prompt_system_path = prompt_system_path
        self._prompt_user_path = prompt_user_path
        self._prompt_web_search_system_path = prompt_web_search_system_path
        self._prompt_web_search_user_path = prompt_web_search_user_path
        self._prompt_web_search_template_path = prompt_web_search_template_path

    def handle(self, payload: dict, attrs: dict) -> bool:
        # Reset ContextVars at handler entry — Pub/Sub SDK reuses threads
        ctx_workflow.set("ects")
        ctx_message_id.set(attrs.get("message_id", "?"))
        ctx_ticker.set("?")

        try:
            msg = ECTSMessage(**payload)
        except Exception:
            log.error("ects_malformed_message", extra={"payload": payload})
            return True  # ack malformed

        ctx_ticker.set(msg.ticker)

        if self._web_search_flag:
            return self._handle_web_search(msg)
        return self._handle_data_mode(msg)

    def _handle_data_mode(self, msg: ECTSMessage) -> bool:
        try:
            processed = self._processor.load_and_process(msg)
        except MissingDataError as e:
            log.error(
                "ects_missing_data",
                extra={"ticker": e.ticker, "missing": e.missing_sources},
            )
            return True  # ack: data not arrived, no point retrying same msg
        except DataParseError as e:
            log.error("ects_data_parse_error", extra={"error": str(e)})
            return True  # ack: data corrupt, redelivery won't fix

        try:
            system, user = build_ects_prompt(
                processed,
                system_template_path=self._prompt_system_path,
                user_template_path=self._prompt_user_path,
            )
            summary = self._claude.complete(system=system, user_prompt=user)
        except ClaudeAPIRetryExhaustedError as e:
            log.error("ects_claude_exhausted", extra={"error": str(e)})
            return True  # ack: service down, don't redeliver

        return self._publish(msg, summary)

    def _handle_web_search(self, msg: ECTSMessage) -> bool:
        try:
            system, user = build_ects_web_search_prompt(
                msg,
                company_name=None,
                stocktitan_news_url=self._stocktitan_news_url,
                motley_fool_url=self._motley_fool_url,
                system_template_path=self._prompt_web_search_system_path,
                user_template_path=self._prompt_web_search_user_path,
                template_path=self._prompt_web_search_template_path,
            )
            summary = self._claude.complete(
                system=system,
                user_prompt=user,
                tools=[web_search_tool(self._web_search_max_uses)],
            )
        except ClaudeAPIRetryExhaustedError as e:
            log.error("ects_claude_exhausted", extra={"error": str(e)})
            return True

        if "ECTS_SOURCES_NOT_AVAILABLE" in summary:
            log.warning("ects_sources_not_available")
            return True  # ack: nothing useful published yet

        return self._publish(msg, summary)

    def _publish(self, msg: ECTSMessage, summary: str) -> bool:
        try:
            self._write_output(msg, summary)
        except GCSWriteError as e:
            log.error("ects_write_failed", extra={"error": str(e)})
            return False  # nack: transient, retry

        log.info("ects_summary_complete")
        return True

    def _write_output(self, msg: ECTSMessage, content: str) -> None:
        path = (
            f"{self._output_prefix}/company={msg.ticker}/"
            f"quarter={msg.fiscal_quarter}/fiscal={msg.fiscal_year}/"
            f"{msg.ticker}_FY_{msg.fiscal_quarter}_{msg.fiscal_year}.md"
        )
        self._gcs.write_text(self._output_bucket, path, content)
