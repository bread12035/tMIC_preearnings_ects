"""Entry point for the ECTS deployment."""

from __future__ import annotations

import asyncio
import logging
import signal

from common.claude_client import ClaudeClient
from common.config import bootstrap_env, get_settings
from common.gcs_service import GCSService
from common.logging import setup_logging
from common.pubsub import AsyncSubscriber
from ects.data_processor import ECTSDataProcessor
from ects.worker import ECTSWorker


async def amain() -> None:
    bootstrap_env()  # MUST be first
    settings = get_settings()
    setup_logging(settings.log_level)
    log = logging.getLogger(__name__)

    if settings.app_mode != "ects":
        raise RuntimeError(
            f"ects.main launched with APP_MODE={settings.app_mode!r}"
        )

    log.info("startup", extra={"safe_settings": settings.safe_dict()})

    gcs = GCSService(
        settings.gcs_project_id, settings.gcs_custom_storage_endpoint
    )
    claude = ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_model_max_tokens,
        base_url=settings.anthropic_api_base_url,
        timeout_seconds=settings.anthropic_request_timeout_seconds,
        max_retries=settings.anthropic_max_retries,
        retry_base_delay=settings.anthropic_retry_base_delay_seconds,
    )

    processor = ECTSDataProcessor(
        gcs,
        bucket_transcript=settings.gcs_bucket_ects_transcript,
        prefix_transcript=settings.gcs_blob_prefix_ects_transcript,
        bucket_financial=settings.gcs_bucket_ects_financial,
        prefix_financial=settings.gcs_blob_prefix_ects_financial,
        bucket_segment=settings.gcs_bucket_ects_segment,
        prefix_segment=settings.gcs_blob_prefix_ects_segment,
        bucket_config=settings.gcs_bucket_company_config,
        prefix_config=settings.gcs_blob_prefix_ects_config,
    )

    worker = ECTSWorker(
        processor=processor,
        claude=claude,
        gcs=gcs,
        output_bucket=settings.gcs_bucket_ects_output,
        output_prefix=settings.gcs_blob_prefix_ects_output,
        web_search_flag=settings.ects_web_search_flag,
        web_search_max_uses=settings.anthropic_web_search_max_uses,
        stocktitan_news_url=settings.stocktitan_news_url,
        motley_fool_url=settings.motley_fool_url,
        prompt_system_path=settings.prompt_ects_system_path,
        prompt_user_path=settings.prompt_ects_user_path,
        prompt_web_search_system_path=settings.prompt_ects_web_search_system_path,
        prompt_web_search_user_path=settings.prompt_ects_web_search_user_path,
        prompt_web_search_template_path=settings.prompt_ects_web_search_template_path,
    )

    subscriber = AsyncSubscriber(
        project_id=settings.gcp_project_id,
        subscription=settings.gcp_pubsub_subscription,
        handler=worker.handle,
        max_inflight=settings.gcp_pubsub_max_inflight,
    )

    # Graceful shutdown
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    _touch("/tmp/alive")

    await subscriber.start()
    _touch("/tmp/ready")
    log.info("subscriber_ready")

    try:
        await stop_event.wait()
    finally:
        await subscriber.shutdown()
        log.info("shutdown_complete")


def _touch(path: str) -> None:
    try:
        with open(path, "w") as f:
            f.write("ok")
    except OSError:
        pass


if __name__ == "__main__":
    asyncio.run(amain())
