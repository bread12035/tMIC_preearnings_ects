"""Entry point for the pre-earnings deployment."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from common.claude_client import ClaudeClient
from common.company_config import CompanyConfigLoader
from common.config import bootstrap_env, get_settings
from common.gcs_service import GCSService
from common.logging import setup_logging
from common.pubsub import AsyncSubscriber
from pre_earnings.monitor import PreEarningsMonitor
from pre_earnings.worker import PreEarningsWorker


async def amain() -> None:
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
    )
    worker = PreEarningsWorker(monitor)

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
            # Windows local dev: signal handlers not supported on the proactor loop
            pass

    # Health probe sentinels (paired with K8s exec probes)
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
        # Probe files are best-effort; running outside K8s is fine.
        pass


if __name__ == "__main__":
    asyncio.run(amain())
