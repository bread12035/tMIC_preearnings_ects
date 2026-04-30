"""Entry point for the pre-earnings deployment (sync)."""

from __future__ import annotations

import logging
import signal
import threading

from common.claude_client import ClaudeClient
from common.company_config import CompanyConfigLoader
from common.config import bootstrap_env, get_settings
from common.gcs_service import GCSService
from common.logging import setup_logging
from common.sync_subscriber import SyncSubscriber
from pre_earnings.monitor import PreEarningsMonitor
from pre_earnings.worker import PreEarningsWorker


def main() -> None:
    bootstrap_env()  # MUST be first: load /app/.env (or repo-root .env)
    settings = get_settings()
    setup_logging(settings.log_level)
    log = logging.getLogger(__name__)

    if settings.app_mode != "pre_earnings":
        raise RuntimeError(
            f"pre_earnings.main launched with APP_MODE={settings.app_mode!r}"
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
    config_loader = CompanyConfigLoader(
        gcs,
        settings.gcs_bucket_company_config,
        settings.gcs_blob_prefix_pre_earnings_config,
    )
    monitor = PreEarningsMonitor(
        gcs,
        claude,
        config_loader,
        output_bucket=settings.gcs_bucket_pre_earnings_output,
        output_prefix=settings.gcs_blob_prefix_pre_earnings_output,
        web_search_max_uses=settings.anthropic_web_search_max_uses,
        stocktitan_news_url=settings.stocktitan_news_url,
        prompt_system_path=settings.prompt_pre_earnings_system_path,
        prompt_user_path=settings.prompt_pre_earnings_user_path,
    )
    worker = PreEarningsWorker(monitor)

    subscriber = SyncSubscriber(
        project_id=settings.gcp_project_id,
        subscription=settings.gcp_pubsub_subscription,
        handler=worker.handle,
        max_messages=settings.gcp_pubsub_max_inflight,
        max_lease_duration=10800,  # 3h — covers worst-case polling window (SDD §3.2)
    )

    # Bridge signal handler to shutdown via threading.Event.
    # Signal handler must do as little as possible — just set the event.
    shutdown_event = threading.Event()

    def _on_signal(signum, frame):
        log.info("shutdown_signal_received", extra={"signal": signum})
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # Health probe sentinels (paired with K8s exec probes)
    _touch("/tmp/ready")

    subscriber.start()
    log.info("subscriber_ready")

    # Poll shutdown_event; also exit if the streaming future dies on its own.
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
