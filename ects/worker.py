"""Pub/Sub message handler for ECTS workflow."""

from __future__ import annotations

import logging

from common.claude_client import ClaudeClient
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
from ects.prompt_builder import build_ects_prompt

log = logging.getLogger(__name__)


class ECTSWorker:
    def __init__(
        self,
        processor: ECTSDataProcessor,
        claude: ClaudeClient,
        gcs: GCSService,
        output_bucket: str,
        output_prefix: str,
    ):
        self._processor = processor
        self._claude = claude
        self._gcs = gcs
        self._output_bucket = output_bucket
        self._output_prefix = output_prefix

    async def handle(self, payload: dict, attrs: dict) -> bool:
        ctx_workflow.set("ects")
        ctx_message_id.set(attrs.get("message_id", "?"))

        try:
            msg = ECTSMessage(**payload)
        except Exception:
            log.error("ects_malformed_message", extra={"payload": payload})
            return True  # ack malformed

        ctx_ticker.set(msg.ticker)

        try:
            processed = await self._processor.load_and_process(msg)
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
            system, user = build_ects_prompt(processed)
            summary = await self._claude.complete(system=system, user_prompt=user)
        except ClaudeAPIRetryExhaustedError as e:
            log.error("ects_claude_exhausted", extra={"error": str(e)})
            return True  # ack: service down, don't redeliver

        try:
            await self._write_output(msg, summary)
        except GCSWriteError as e:
            log.error("ects_write_failed", extra={"error": str(e)})
            return False  # nack: transient, retry

        log.info("ects_summary_complete")
        return True

    async def _write_output(self, msg: ECTSMessage, content: str) -> None:
        path = (
            f"{self._output_prefix}/company={msg.ticker}/"
            f"quarter={msg.fiscal_quarter}/fiscal={msg.fiscal_year}/"
            f"{msg.ticker}_FY_{msg.fiscal_quarter}_{msg.fiscal_year}.md"
        )
        await self._gcs.write_text(self._output_bucket, path, content)
