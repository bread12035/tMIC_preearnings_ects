"""Entry point for the ECTS deployment (sync)."""

from __future__ import annotations

import logging
import signal
import threading

from common.claude_client import ClaudeClient
from common.config import bootstrap_env, get_settings
from common.gcs_service import GCSService
from common.logging import setup_logging
from common.sync_subscriber import SyncSubscriber
from ects.data_processor import ECTSDataProcessor
from ects.worker import ECTSWorker


def main() -> None:
    bootstrap_env()  # MUST be first
    settings = get_settings()
    setup_logging(settings.log_level)
    log = logging.getLogger(__name__)

    if settings.app_mode != "ects":
        raise RuntimeError(
            f"ects.main launched with APP_MODE={settings.app_mode!r}"
        )

    log.info("startup", extra={"safe_settings": settings.safe_dict()})

    gcs = GCSService(settings.gcs_project_id, settings.gcs_custom_storage_endpoint)
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

    subscriber = SyncSubscriber(
        project_id=settings.gcp_project_id,
        subscription=settings.gcp_pubsub_subscription,
        handler=worker.handle,
        max_messages=settings.gcp_pubsub_max_inflight,
        # ECTS is one-shot; default 3600s lease is plenty
    )

    shutdown_event = threading.Event()

    def _on_signal(signum, frame):
        log.info("shutdown_signal_received", extra={"signal": signum})
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    _touch("/tmp/ready")

    subscriber.start()
    log.info("subscriber_ready")

    while not shutdown_event.is_set():
        if shutdown_event.wait(timeout=1.0):
            break
        if (
            subscriber._streaming_future is not None
            and subscriber._streaming_future.done()
        ):
            log.warning("streaming_future_done_unexpectedly")
            break

    subscriber.shutdown()
    log.info("shutdown_complete")


def _touch(path: str) -> None:
    try:
        with open(path, "w") as f:
            f.write("ok")
    except OSError:
        pass


if __name__ == "__main__":
    main()
