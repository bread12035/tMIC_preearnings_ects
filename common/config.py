"""
Settings loader.

Boot sequence:
  1. main.py calls bootstrap_env() FIRST, before any other import that reads env.
  2. bootstrap_env() runs load_dotenv() (no path) - looks in CWD for `.env`.
     - Local dev: repo root `.env`
     - K8s: WORKDIR=/app, file is /app/.env (assembled by initContainer)
  3. get_settings() reads os.getenv(...) and returns a validated Settings instance.

We intentionally DO NOT use pydantic-settings; the user requested plain
load_dotenv + os.getenv. We keep a small dataclass for type safety + a single
validation pass that fails fast on missing required vars.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv

log = logging.getLogger(__name__)


def bootstrap_env() -> None:
    """
    Call ONCE at process startup, before importing anything that reads env vars.

    load_dotenv() with no args:
      - searches CWD then walks up
      - does NOT override existing env vars (so K8s env wins over .env if both set)

    Idempotent: safe to call multiple times.
    """
    load_dotenv()


# --- helpers --------------------------------------------------------------

def _require(key: str) -> str:
    val = os.getenv(key)
    if val is None or val == "":
        raise RuntimeError(f"Required env var {key} is not set")
    return val


def _optional(key: str, default: str | None = None) -> str | None:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    return val


def _int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Env var {key}={raw!r} is not a valid int") from e


def _bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    val = raw.strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    raise RuntimeError(f"Env var {key}={raw!r} is not a valid bool")


# --- Settings -------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    # App
    app_mode: Literal["pre_earnings", "ects", "calendar_sync", "task_dispatcher"]
    log_level: str
    environment: str

    # GCP / Pub/Sub
    gcp_project_id: str
    gcp_pubsub_topic: str
    gcp_pubsub_subscription: str | None
    gcp_pubsub_max_inflight: int
    gcp_pubsub_ack_deadline_seconds: int

    # GCS
    gcs_project_id: str
    gcs_custom_storage_endpoint: str | None

    gcs_bucket_pre_earnings_output: str
    gcs_blob_prefix_pre_earnings_output: str
    gcs_bucket_ects_output: str
    gcs_blob_prefix_ects_output: str

    gcs_bucket_ects_transcript: str
    gcs_blob_prefix_ects_transcript: str
    gcs_bucket_ects_financial: str
    gcs_blob_prefix_ects_financial: str
    gcs_bucket_ects_segment: str
    gcs_blob_prefix_ects_segment: str

    gcs_bucket_company_config: str
    gcs_blob_prefix_pre_earnings_config: str
    gcs_blob_prefix_ects_config: str

    # Claude
    anthropic_api_key: str  # SECRET - never log this directly
    anthropic_model: str
    anthropic_model_max_tokens: int
    anthropic_api_base_url: str
    anthropic_request_timeout_seconds: int
    anthropic_max_retries: int
    anthropic_retry_base_delay_seconds: int
    anthropic_web_search_max_uses: int

    # Pre-earnings defaults
    pre_earnings_default_start_offset_minutes: int
    pre_earnings_default_poll_interval_minutes: int
    pre_earnings_default_max_attempts: int

    # Web search sources (used by both workflows)
    stocktitan_news_url: str
    motley_fool_url: str

    # ECTS web-search mode
    ects_web_search_flag: bool

    # Prompt template paths (rendered via str.format on disk).
    # Default to the in-image `prompts/` dir; CD may override individual paths
    # to point at ConfigMap-mounted overrides.
    prompt_pre_earnings_system_path: str
    prompt_pre_earnings_user_path: str
    prompt_ects_system_path: str
    prompt_ects_user_path: str
    prompt_ects_web_search_system_path: str
    prompt_ects_web_search_user_path: str
    prompt_ects_web_search_template_path: str

    # Event calendar (calendar_sync + task_dispatcher CronJobs)
    event_calendar_watchlist_bucket: str | None
    event_calendar_watchlist_blob: str
    event_calendar_registry_bucket: str | None
    event_calendar_registry_prefix: str
    event_calendar_lookahead_days: int
    event_calendar_pre_earnings_offset_minutes: int
    event_calendar_ects_offset_minutes: int
    event_calendar_dispatch_window_minutes: int

    def safe_dict(self) -> dict:
        """Return all fields with the API key redacted, for log/debug."""
        d = {f: getattr(self, f) for f in self.__dataclass_fields__}
        if d.get("anthropic_api_key"):
            d["anthropic_api_key"] = "sk-ant-***REDACTED***"
        return d


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Read env vars (already loaded by bootstrap_env) into a validated Settings.
    Cached so repeated calls don't re-read os.environ.
    """
    app_mode = _require("APP_MODE")
    valid_modes = ("pre_earnings", "ects", "calendar_sync", "task_dispatcher")
    if app_mode not in valid_modes:
        raise RuntimeError(
            f"APP_MODE must be one of {valid_modes}, got {app_mode!r}"
        )

    # Subscription is only required for the two consumer Deployments
    if app_mode in ("pre_earnings", "ects"):
        gcp_pubsub_subscription: str | None = _require("GCP_PUBSUB_SUBSCRIPTION")
    else:
        gcp_pubsub_subscription = _optional("GCP_PUBSUB_SUBSCRIPTION")

    return Settings(
        app_mode=app_mode,  # type: ignore[arg-type]
        log_level=_optional("LOG_LEVEL", "INFO"),  # type: ignore[arg-type]
        environment=_optional("ENVIRONMENT", "production"),  # type: ignore[arg-type]
        gcp_project_id=_require("GCP_PROJECT_ID"),
        gcp_pubsub_topic=_require("GCP_PUBSUB_TOPIC"),
        gcp_pubsub_subscription=gcp_pubsub_subscription,
        gcp_pubsub_max_inflight=_int("GCP_PUBSUB_MAX_INFLIGHT", 20),
        gcp_pubsub_ack_deadline_seconds=_int("GCP_PUBSUB_ACK_DEADLINE_SECONDS", 600),
        gcs_project_id=_require("GCS_PROJECT_ID"),
        gcs_custom_storage_endpoint=_optional("GCS_CUSTOM_STORAGE_ENDPOINT"),
        gcs_bucket_pre_earnings_output=_require("GCS_BUCKET_PRE_EARNINGS_OUTPUT"),
        gcs_blob_prefix_pre_earnings_output=_require(
            "GCS_BLOB_PREFIX_PRE_EARNINGS_OUTPUT"
        ),
        gcs_bucket_ects_output=_require("GCS_BUCKET_ECTS_OUTPUT"),
        gcs_blob_prefix_ects_output=_require("GCS_BLOB_PREFIX_ECTS_OUTPUT"),
        gcs_bucket_ects_transcript=_require("GCS_BUCKET_ECTS_TRANSCRIPT"),
        gcs_blob_prefix_ects_transcript=_require("GCS_BLOB_PREFIX_ECTS_TRANSCRIPT"),
        gcs_bucket_ects_financial=_require("GCS_BUCKET_ECTS_FINANCIAL"),
        gcs_blob_prefix_ects_financial=_require("GCS_BLOB_PREFIX_ECTS_FINANCIAL"),
        gcs_bucket_ects_segment=_require("GCS_BUCKET_ECTS_SEGMENT"),
        gcs_blob_prefix_ects_segment=_require("GCS_BLOB_PREFIX_ECTS_SEGMENT"),
        gcs_bucket_company_config=_require("GCS_BUCKET_COMPANY_CONFIG"),
        gcs_blob_prefix_pre_earnings_config=_require(
            "GCS_BLOB_PREFIX_PRE_EARNINGS_CONFIG"
        ),
        gcs_blob_prefix_ects_config=_require("GCS_BLOB_PREFIX_ECTS_CONFIG"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        anthropic_model=_require("ANTHROPIC_MODEL"),
        anthropic_model_max_tokens=_int("ANTHROPIC_MODEL_MAX_TOKENS", 8192),
        anthropic_api_base_url=_optional(  # type: ignore[arg-type]
            "ANTHROPIC_API_BASE_URL", "https://api.anthropic.com"
        ),
        anthropic_request_timeout_seconds=_int(
            "ANTHROPIC_REQUEST_TIMEOUT_SECONDS", 120
        ),
        anthropic_max_retries=_int("ANTHROPIC_MAX_RETRIES", 5),
        anthropic_retry_base_delay_seconds=_int(
            "ANTHROPIC_RETRY_BASE_DELAY_SECONDS", 2
        ),
        anthropic_web_search_max_uses=_int("ANTHROPIC_WEB_SEARCH_MAX_USES", 10),
        pre_earnings_default_start_offset_minutes=_int(
            "PRE_EARNINGS_DEFAULT_START_OFFSET_MINUTES", 30
        ),
        pre_earnings_default_poll_interval_minutes=_int(
            "PRE_EARNINGS_DEFAULT_POLL_INTERVAL_MINUTES", 10
        ),
        pre_earnings_default_max_attempts=_int(
            "PRE_EARNINGS_DEFAULT_MAX_ATTEMPTS", 12
        ),
        stocktitan_news_url=_optional(  # type: ignore[arg-type]
            "STOCKTITAN_NEWS_URL", "https://www.stocktitan.net/news"
        ),
        motley_fool_url=_optional(  # type: ignore[arg-type]
            "MOTLEY_FOOL_URL", "https://www.fool.com/earnings-call-transcripts"
        ),
        ects_web_search_flag=_bool("ECTS_WEB_SEARCH_FLAG", False),
        prompt_pre_earnings_system_path=_optional(  # type: ignore[arg-type]
            "PROMPT_PRE_EARNINGS_SYSTEM_PATH",
            "prompts/pre_earnings_system.md.tmpl",
        ),
        prompt_pre_earnings_user_path=_optional(  # type: ignore[arg-type]
            "PROMPT_PRE_EARNINGS_USER_PATH",
            "prompts/pre_earnings_user.md.tmpl",
        ),
        prompt_ects_system_path=_optional(  # type: ignore[arg-type]
            "PROMPT_ECTS_SYSTEM_PATH", "prompts/ects_system.md.tmpl"
        ),
        prompt_ects_user_path=_optional(  # type: ignore[arg-type]
            "PROMPT_ECTS_USER_PATH", "prompts/ects_user.md.tmpl"
        ),
        prompt_ects_web_search_system_path=_optional(  # type: ignore[arg-type]
            "PROMPT_ECTS_WEB_SEARCH_SYSTEM_PATH",
            "prompts/ects_web_search_system.md.tmpl",
        ),
        prompt_ects_web_search_user_path=_optional(  # type: ignore[arg-type]
            "PROMPT_ECTS_WEB_SEARCH_USER_PATH",
            "prompts/ects_web_search_user.md.tmpl",
        ),
        prompt_ects_web_search_template_path=_optional(  # type: ignore[arg-type]
            "PROMPT_ECTS_WEB_SEARCH_TEMPLATE_PATH",
            "prompts/ects_web_search_template.md.tmpl",
        ),
        event_calendar_watchlist_bucket=_optional(
            "EVENT_CALENDAR_WATCHLIST_BUCKET"
        ),
        event_calendar_watchlist_blob=_optional(  # type: ignore[arg-type]
            "EVENT_CALENDAR_WATCHLIST_BLOB", "configs/watchlist.json"
        ),
        event_calendar_registry_bucket=_optional(
            "EVENT_CALENDAR_REGISTRY_BUCKET"
        ),
        event_calendar_registry_prefix=_optional(  # type: ignore[arg-type]
            "EVENT_CALENDAR_REGISTRY_PREFIX", "configs/event_calendar"
        ),
        event_calendar_lookahead_days=_int("EVENT_CALENDAR_LOOKAHEAD_DAYS", 14),
        event_calendar_pre_earnings_offset_minutes=_int(
            "EVENT_CALENDAR_PRE_EARNINGS_OFFSET_MINUTES", -30
        ),
        event_calendar_ects_offset_minutes=_int(
            "EVENT_CALENDAR_ECTS_OFFSET_MINUTES", 30
        ),
        event_calendar_dispatch_window_minutes=_int(
            "EVENT_CALENDAR_DISPATCH_WINDOW_MINUTES", 10
        ),
    )
