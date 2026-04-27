# SDD: Earnings Intelligence Service on GKE

> Two event-driven workflows (`pre-earnings`, `ects`) sharing common infrastructure (GCS, Pub/Sub, Claude API), deployed as two GKE Deployments from a single codebase.

---

## 1. Architecture Overview

### 1.1 High-level

```
                      ┌─────────────────────────┐
                      │  Pub/Sub Topic          │
                      │  (single, shared)       │
                      └───────────┬─────────────┘
                                  │
                ┌─────────────────┴─────────────────┐
                │                                   │
        ┌───────▼─────────┐                ┌────────▼─────────┐
        │ Subscription A  │                │ Subscription B   │
        │ filter:         │                │ filter:          │
        │ event_type=     │                │ event_type=      │
        │ "pre_earnings"  │                │ "ects"           │
        └───────┬─────────┘                └────────┬─────────┘
                │                                   │
        ┌───────▼─────────┐                ┌────────▼─────────┐
        │ pre-earnings    │                │ ects             │
        │ Deployment      │                │ Deployment       │
        │ (Pod x N)       │                │ (Pod x M)        │
        └───────┬─────────┘                └────────┬─────────┘
                │                                   │
                │         ┌───────────────┐         │
                ├────────►│  GCS          │◄────────┤
                │         │  (input/      │         │
                │         │   output)     │         │
                │         └───────────────┘         │
                │                                   │
                │         ┌───────────────┐         │
                └────────►│  Claude API   │◄────────┘
                          │ (+ web_search │
                          │   for         │
                          │   pre-        │
                          │   earnings)   │
                          └───────────────┘
```

### 1.2 Workflow comparison

| Aspect | pre-earnings | ects |
|--------|--------------|------|
| Trigger | Pub/Sub msg (event calendar T-30min) | Pub/Sub msg (after earnings call) |
| Input source | Company official website (via Claude web_search) | GCS parquet (Bloomberg) |
| Processing | Periodic polling (in-memory loop) | One-shot batch |
| LLM call | Claude API + web_search tool | Claude API (text only) |
| Ack timing | **Ack on receive** (poll runs in background) | **Ack on success or after retry exhaust** |
| Failure handling | If poll fails after N attempts, log + GCS audit | If LLM fails 5 retries, ack (treat as down) |
| Output | GCS markdown | GCS markdown |
| Concurrency profile | Many long-running tasks (mostly sleeping) | Bursty CPU/IO |

### 1.3 Shared GCS path convention

```
digwork/tmic/{workflow}/company={ticker}/quarter={fiscal_quarter}/fiscal={fiscal_year}/{ticker}_FY_{fiscal_quarter}_{fiscal_year}.md
```

- `{workflow}` ∈ `{pre_earnings_summary, ects_summary}`
- Overwrite-on-write (no historical retention)

---

## 2. Repository Layout

```
earnings-intel/
├── common/                          # Shared by both workflows
│   ├── __init__.py
│   ├── config.py                    # Settings (Pydantic BaseSettings) loaded from env
│   ├── logging.py                   # Structured JSON logger
│   ├── exceptions.py                # Custom exception hierarchy
│   ├── gcs_service.py               # Async GCS read/write
│   ├── pubsub.py                    # AsyncSubscriber bridge
│   ├── claude_client.py             # Async Claude wrapper with retry
│   └── company_config.py            # Per-company config loader (from GCS)
│
├── pre_earnings/
│   ├── __init__.py
│   ├── main.py                      # Entry point (asyncio.run)
│   ├── worker.py                    # Pub/Sub message handler
│   ├── monitor.py                   # In-memory polling loop
│   ├── prompt_builder.py            # Build prompt from company config
│   └── models.py                    # Pydantic models (PreEarningsMessage, ...)
│
├── ects/
│   ├── __init__.py
│   ├── main.py                      # Entry point (asyncio.run)
│   ├── worker.py                    # Pub/Sub message handler
│   ├── data_processor.py            # GCS pull + parquet -> DataFrame  (USER FILLS LATER)
│   ├── prompt_builder.py            # Build prompt from processed data
│   └── models.py                    # Pydantic models (ECTSMessage, ...)
│
├── tests/
│   ├── conftest.py                  # Shared fixtures (fake GCS, fake Pub/Sub)
│   ├── unit/
│   │   ├── test_gcs_service.py
│   │   ├── test_pubsub.py
│   │   ├── test_claude_client.py
│   │   ├── test_pre_earnings_worker.py
│   │   ├── test_pre_earnings_monitor.py
│   │   ├── test_ects_worker.py
│   │   └── test_ects_data_processor.py
│   └── integration/
│       ├── test_pre_earnings_e2e.py # Uses Pub/Sub emulator + fake GCS
│       └── test_ects_e2e.py
│
├── deploy/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── configmap-shared.yaml         # All non-sensitive shared env (committed)
│   ├── configmap-pre-earnings.yaml   # APP_MODE + subscription override (committed)
│   ├── configmap-ects.yaml           # APP_MODE + subscription override (committed)
│   ├── secret.yaml.example           # Shape only — real values not committed
│   ├── service-account.yaml          # KSA + Workload Identity binding
│   ├── pre-earnings-deployment.yaml
│   ├── ects-deployment.yaml
│   └── kustomization.yaml
│
├── configs/
│   └── pre_earnings/
│       └── AAPL.example.json        # Example per-company config
│
├── .env.example
├── requirements.txt
├── README.md
└── SDD.md
```

---

## 3. Configuration

### 3.1 Loading model

**Single source of truth: `/app/.env`** (relative to `WORKDIR`).

The application calls `load_dotenv()` with no arguments at startup. Python-dotenv looks for `.env` in the current working directory (and walks up if not found). This gives us:

- **Local dev**: place `.env` at repo root, `python -m pre_earnings.main` finds it automatically.
- **GKE production**: container's `WORKDIR=/app`, `.env` is composed at `/app/.env` by an `initContainer` that concatenates a ConfigMap (non-sensitive) and a Secret (sensitive).

After `load_dotenv()`, all reads go through `os.getenv("KEY", default)` — no `pydantic-settings`, no magic. Settings are validated explicitly in `common/config.py`.

**Provenance of each variable**:

| Variable | Source in K8s | Sensitivity |
|----------|--------------|-------------|
| `APP_MODE`, `LOG_LEVEL`, `ENVIRONMENT` | ConfigMap (per-deployment override) | non-sensitive |
| `GCP_PROJECT_ID`, `GCP_PUBSUB_*` | ConfigMap | non-sensitive |
| `GCS_PROJECT_ID`, `GCS_BUCKET_*`, `GCS_BLOB_PREFIX_*` | ConfigMap | non-sensitive |
| `ANTHROPIC_MODEL`, `ANTHROPIC_*_TIMEOUT_SECONDS`, `ANTHROPIC_API_BASE_URL`, all retry / token / web_search params | ConfigMap | non-sensitive |
| `ANTHROPIC_API_KEY` | **Secret** | sensitive |
| `PRE_EARNINGS_DEFAULT_*` | ConfigMap | non-sensitive |

> **Two ConfigMaps**, not one: a shared `earnings-intel-config` for everything common, and a small per-workflow `earnings-intel-{pre-earnings,ects}-config` that sets `APP_MODE` and `GCP_PUBSUB_SUBSCRIPTION`. The initContainer concatenates: shared ConfigMap → workflow ConfigMap → Secret.

### 3.2 `.env.example`

This file lives at repo root and is the canonical reference. Local devs copy it to `.env` and fill in real values. **Add `.env` to `.gitignore`.**

```bash
# ─────────────────────────────────────────────────────────────
# Application mode (selects entry point)
# Source: per-workflow ConfigMap (different value per Deployment)
# ─────────────────────────────────────────────────────────────
APP_MODE=pre_earnings              # or "ects"
LOG_LEVEL=INFO                     # DEBUG | INFO | WARNING | ERROR
ENVIRONMENT=production             # local | dev | staging | production

# ─────────────────────────────────────────────────────────────
# GCP project & Pub/Sub
# Source: shared ConfigMap (GCP_PUBSUB_SUBSCRIPTION overridden by per-workflow ConfigMap)
# ─────────────────────────────────────────────────────────────
GCP_PROJECT_ID=my-gcp-project
GCP_PUBSUB_TOPIC=earnings-events
GCP_PUBSUB_SUBSCRIPTION=earnings-events-pre-earnings-sub   # set per workflow
GCP_PUBSUB_MAX_INFLIGHT=20         # max concurrently-processed msgs per pod
GCP_PUBSUB_ACK_DEADLINE_SECONDS=600

# ─────────────────────────────────────────────────────────────
# GCS (storage)
# Source: shared ConfigMap
# ─────────────────────────────────────────────────────────────
GCS_PROJECT_ID=my-gcp-project
GCS_CUSTOM_STORAGE_ENDPOINT=       # optional override; empty = default

# Output buckets/blobs (each workflow writes to its own)
GCS_BUCKET_PRE_EARNINGS_OUTPUT=tmic-prod-pre-earnings
GCS_BLOB_PREFIX_PRE_EARNINGS_OUTPUT=digwork/tmic/pre_earnings_summary

GCS_BUCKET_ECTS_OUTPUT=tmic-prod-ects
GCS_BLOB_PREFIX_ECTS_OUTPUT=digwork/tmic/ects_summary

# Input buckets/blobs (ects only, sourced from Bloomberg pipeline)
GCS_BUCKET_ECTS_TRANSCRIPT=tmic-prod-bbg-transcript
GCS_BLOB_PREFIX_ECTS_TRANSCRIPT=bbg/transcript

GCS_BUCKET_ECTS_FINANCIAL=tmic-prod-bbg-financial
GCS_BLOB_PREFIX_ECTS_FINANCIAL=bbg/financial

GCS_BUCKET_ECTS_SEGMENT=tmic-prod-bbg-segment
GCS_BLOB_PREFIX_ECTS_SEGMENT=bbg/segment

# Company config (per-company JSON, loaded from GCS)
GCS_BUCKET_COMPANY_CONFIG=tmic-prod-company-config
GCS_BLOB_PREFIX_PRE_EARNINGS_CONFIG=configs/pre_earnings
GCS_BLOB_PREFIX_ECTS_CONFIG=configs/ects

# ─────────────────────────────────────────────────────────────
# Claude API
# Source: ANTHROPIC_API_KEY → K8s Secret (mounted to /app/.env via initContainer)
#         All other Claude vars → shared ConfigMap
# ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-xxx       # SECRET — never commit a real value
ANTHROPIC_MODEL=claude-opus-4-7
ANTHROPIC_MODEL_MAX_TOKENS=8192
ANTHROPIC_API_BASE_URL=https://api.anthropic.com
ANTHROPIC_REQUEST_TIMEOUT_SECONDS=120
ANTHROPIC_MAX_RETRIES=5
ANTHROPIC_RETRY_BASE_DELAY_SECONDS=2     # exponential: 2, 4, 8, 16, 32

# Pre-earnings only: web_search tool config
ANTHROPIC_WEB_SEARCH_MAX_USES=10

# ─────────────────────────────────────────────────────────────
# Pre-earnings polling defaults (overridable per-company)
# ─────────────────────────────────────────────────────────────
PRE_EARNINGS_DEFAULT_START_OFFSET_MINUTES=30
PRE_EARNINGS_DEFAULT_POLL_INTERVAL_MINUTES=10
PRE_EARNINGS_DEFAULT_MAX_ATTEMPTS=12        # ~2hr coverage at 10min interval
```

> **Note**: `GCP_PUBSUB_SUBSCRIPTION` is the **only** env var that differs between the two Deployments at the platform level. Everything else either stays the same or is keyed by `APP_MODE`.

### 3.2 Per-company pre-earnings config (example)

`configs/pre_earnings/AAPL.example.json`:

```json
{
  "ticker": "AAPL",
  "company_name": "Apple Inc.",

  "press_release_urls": [
    "https://www.apple.com/newsroom/",
    "https://investor.apple.com/investor-relations/default.aspx"
  ],

  "financial_topics": [
    "iPhone revenue",
    "Services revenue",
    "Greater China revenue",
    "Gross margin",
    "Operating income by segment",
    "EPS (diluted)",
    "Capital return (buyback + dividend)",
    "Forward guidance"
  ],

  "polling": {
    "start_offset_minutes": 30,
    "interval_minutes": 5,
    "max_attempts": 24
  },

  "summary_template": {
    "language": "en",
    "sections": [
      "Headline numbers",
      "Segment breakdown",
      "Geographic breakdown",
      "Margin and profitability",
      "Capital return",
      "Forward guidance"
    ],
    "style_guidance": "Concise. Use bullet points. Include YoY % deltas where source provides them. Quote management commentary verbatim only when the original phrasing matters (≤15 words)."
  },

  "prompt_extras": {
    "additional_context": "Apple typically reports Q1 in January (covering Oct-Dec). Pay attention to Services attach rate.",
    "must_check_phrases": ["10-Q", "press release", "Earnings announcement"]
  }
}
```

The config is loaded from GCS at: `gs://{GCS_BUCKET_COMPANY_CONFIG}/{GCS_BLOB_PREFIX_PRE_EARNINGS_CONFIG}/{ticker}.json`.

---

## 4. Common Modules

### 4.1 `common/config.py`

Use `python-dotenv` to load `/app/.env` (or repo-root `.env` for local dev), then read with `os.getenv`. A small `Settings` dataclass adds explicit validation and type coercion so the rest of the codebase doesn't sprinkle `os.getenv` everywhere.

```python
"""
Settings loader.

Boot sequence:
  1. main.py calls bootstrap_env() FIRST, before any other import that reads env.
  2. bootstrap_env() runs load_dotenv() (no path) — looks in CWD for `.env`.
     - Local dev: repo root `.env`
     - K8s: WORKDIR=/app, file is /app/.env (assembled by initContainer)
  3. get_settings() reads os.getenv(...) and returns a validated Settings instance.

We intentionally DO NOT use pydantic-settings; the user requested plain
load_dotenv + os.getenv. We keep a small dataclass for type safety + a single
validation pass that fails fast on missing required vars.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal
import logging
import os
from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Sentinel to distinguish "var unset" from "var set to empty string"
_UNSET = object()


def bootstrap_env() -> None:
    """
    Call ONCE at process startup, before importing anything that reads env vars.

    load_dotenv() with no args:
      - searches CWD then walks up
      - does NOT override existing env vars (so K8s env wins over .env if both set)

    Idempotent: safe to call multiple times.
    """
    load_dotenv()


# ─── helpers ────────────────────────────────────────────────────────────────

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


# ─── Settings ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Settings:
    # App
    app_mode: Literal["pre_earnings", "ects"]
    log_level: str
    environment: str

    # GCP / Pub/Sub
    gcp_project_id: str
    gcp_pubsub_topic: str
    gcp_pubsub_subscription: str
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
    anthropic_api_key: str   # SECRET — never log this directly
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
    if app_mode not in ("pre_earnings", "ects"):
        raise RuntimeError(f"APP_MODE must be 'pre_earnings' or 'ects', got {app_mode!r}")

    return Settings(
        app_mode=app_mode,                                   # type: ignore[arg-type]
        log_level=_optional("LOG_LEVEL", "INFO"),            # type: ignore[arg-type]
        environment=_optional("ENVIRONMENT", "production"),  # type: ignore[arg-type]

        gcp_project_id=_require("GCP_PROJECT_ID"),
        gcp_pubsub_topic=_require("GCP_PUBSUB_TOPIC"),
        gcp_pubsub_subscription=_require("GCP_PUBSUB_SUBSCRIPTION"),
        gcp_pubsub_max_inflight=_int("GCP_PUBSUB_MAX_INFLIGHT", 20),
        gcp_pubsub_ack_deadline_seconds=_int("GCP_PUBSUB_ACK_DEADLINE_SECONDS", 600),

        gcs_project_id=_require("GCS_PROJECT_ID"),
        gcs_custom_storage_endpoint=_optional("GCS_CUSTOM_STORAGE_ENDPOINT"),

        gcs_bucket_pre_earnings_output=_require("GCS_BUCKET_PRE_EARNINGS_OUTPUT"),
        gcs_blob_prefix_pre_earnings_output=_require("GCS_BLOB_PREFIX_PRE_EARNINGS_OUTPUT"),
        gcs_bucket_ects_output=_require("GCS_BUCKET_ECTS_OUTPUT"),
        gcs_blob_prefix_ects_output=_require("GCS_BLOB_PREFIX_ECTS_OUTPUT"),

        gcs_bucket_ects_transcript=_require("GCS_BUCKET_ECTS_TRANSCRIPT"),
        gcs_blob_prefix_ects_transcript=_require("GCS_BLOB_PREFIX_ECTS_TRANSCRIPT"),
        gcs_bucket_ects_financial=_require("GCS_BUCKET_ECTS_FINANCIAL"),
        gcs_blob_prefix_ects_financial=_require("GCS_BLOB_PREFIX_ECTS_FINANCIAL"),
        gcs_bucket_ects_segment=_require("GCS_BUCKET_ECTS_SEGMENT"),
        gcs_blob_prefix_ects_segment=_require("GCS_BLOB_PREFIX_ECTS_SEGMENT"),

        gcs_bucket_company_config=_require("GCS_BUCKET_COMPANY_CONFIG"),
        gcs_blob_prefix_pre_earnings_config=_require("GCS_BLOB_PREFIX_PRE_EARNINGS_CONFIG"),
        gcs_blob_prefix_ects_config=_require("GCS_BLOB_PREFIX_ECTS_CONFIG"),

        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        anthropic_model=_require("ANTHROPIC_MODEL"),
        anthropic_model_max_tokens=_int("ANTHROPIC_MODEL_MAX_TOKENS", 8192),
        anthropic_api_base_url=_optional(                                          # type: ignore[arg-type]
            "ANTHROPIC_API_BASE_URL", "https://api.anthropic.com"
        ),
        anthropic_request_timeout_seconds=_int("ANTHROPIC_REQUEST_TIMEOUT_SECONDS", 120),
        anthropic_max_retries=_int("ANTHROPIC_MAX_RETRIES", 5),
        anthropic_retry_base_delay_seconds=_int("ANTHROPIC_RETRY_BASE_DELAY_SECONDS", 2),
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
    )
```

**Usage rule for callers**:

```python
# pre_earnings/main.py
from common.config import bootstrap_env, get_settings

def amain():
    bootstrap_env()              # FIRST — before anything else
    settings = get_settings()
    # ... use settings.anthropic_api_key etc.
```

> **Why `bootstrap_env()` is a separate step**: `load_dotenv()` only affects `os.environ` — it does *not* override values already set by the OS / K8s. So if some module reads `os.getenv(...)` at *import time*, and you import that module before calling `load_dotenv`, the env value won't be there yet. Putting `bootstrap_env()` as the first line of `amain()` (and never reading env at module level) avoids this trap entirely.


### 4.2 `common/exceptions.py`

```python
class EarningsIntelError(Exception):
    """Base class for all custom exceptions."""

# --- GCS ---
class GCSError(EarningsIntelError): pass
class GCSObjectNotFound(GCSError): pass
class GCSWriteError(GCSError): pass

# --- Pub/Sub ---
class PubSubError(EarningsIntelError): pass
class MessageDecodeError(PubSubError): pass

# --- Claude ---
class ClaudeAPIError(EarningsIntelError): pass
class ClaudeAPIRateLimitError(ClaudeAPIError): pass
class ClaudeAPITimeoutError(ClaudeAPIError): pass
class ClaudeAPIRetryExhaustedError(ClaudeAPIError):
    """Raised after all retries fail; treated as 'service down', ack the message."""

# --- Pre-earnings ---
class PreEarningsError(EarningsIntelError): pass
class PressReleaseNotFoundError(PreEarningsError):
    """Claude returned 'no press release available yet'. Caller should retry on next poll."""
class PollingExhaustedError(PreEarningsError):
    """All polling attempts done, still no press release. Log + audit, ack."""

# --- ECTS ---
class ECTSError(EarningsIntelError): pass
class MissingDataError(ECTSError):
    """Required GCS source(s) not found. Ack + audit log."""
    def __init__(self, ticker: str, missing_sources: list[str]):
        super().__init__(f"Missing data for {ticker}: {missing_sources}")
        self.ticker = ticker
        self.missing_sources = missing_sources
class DataParseError(ECTSError):
    """Source file exists but parquet/JSON parse failed. Ack + alert."""

# --- Config ---
class ConfigError(EarningsIntelError): pass
class CompanyConfigNotFoundError(ConfigError): pass
class CompanyConfigInvalidError(ConfigError): pass
```

### 4.3 `common/logging.py`

Structured JSON logging with context propagation:

```python
import logging
import json
import contextvars
from datetime import datetime, timezone

# Context vars for trace propagation
ctx_ticker = contextvars.ContextVar("ticker", default=None)
ctx_workflow = contextvars.ContextVar("workflow", default=None)
ctx_message_id = contextvars.ContextVar("message_id", default=None)

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "ticker": ctx_ticker.get(),
            "workflow": ctx_workflow.get(),
            "message_id": ctx_message_id.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # extra fields
        for key, val in record.__dict__.items():
            if key not in {"name", "msg", "args", "levelname", "levelno",
                            "pathname", "filename", "module", "exc_info",
                            "exc_text", "stack_info", "lineno", "funcName",
                            "created", "msecs", "relativeCreated", "thread",
                            "threadName", "processName", "process", "message"}:
                payload[key] = val
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Call once at process startup."""
    ...
```

Loggers use `logger.info("msg", extra={"key": val})` to attach structured fields. GKE picks up stdout JSON automatically into Cloud Logging.

### 4.4 `common/gcs_service.py`

```python
from google.cloud import storage
from common.exceptions import GCSObjectNotFound, GCSWriteError
import asyncio

class GCSService:
    """
    Wraps google-cloud-storage (sync client) with asyncio.to_thread for non-blocking IO.
    Uses Workload Identity for auth (no key file).
    """

    def __init__(self, project_id: str, endpoint: str | None = None):
        self._client = storage.Client(
            project=project_id,
            client_options={"api_endpoint": endpoint} if endpoint else None,
        )

    async def read_bytes(self, bucket: str, blob_path: str) -> bytes:
        """Raise GCSObjectNotFound if blob does not exist."""
        return await asyncio.to_thread(self._read_bytes_sync, bucket, blob_path)

    async def read_text(self, bucket: str, blob_path: str, encoding: str = "utf-8") -> str:
        data = await self.read_bytes(bucket, blob_path)
        return data.decode(encoding)

    async def read_json(self, bucket: str, blob_path: str) -> dict:
        text = await self.read_text(bucket, blob_path)
        import json
        return json.loads(text)

    async def read_parquet_bytes(self, bucket: str, blob_path: str) -> bytes:
        return await self.read_bytes(bucket, blob_path)

    async def write_text(
        self, bucket: str, blob_path: str, content: str,
        content_type: str = "text/markdown; charset=utf-8",
    ) -> None:
        await asyncio.to_thread(
            self._write_text_sync, bucket, blob_path, content, content_type
        )

    async def exists(self, bucket: str, blob_path: str) -> bool:
        return await asyncio.to_thread(self._exists_sync, bucket, blob_path)

    # --- sync impls (private) ---
    def _read_bytes_sync(self, bucket: str, blob_path: str) -> bytes:
        from google.cloud.exceptions import NotFound
        try:
            blob = self._client.bucket(bucket).blob(blob_path)
            return blob.download_as_bytes()
        except NotFound:
            raise GCSObjectNotFound(f"gs://{bucket}/{blob_path}")

    def _write_text_sync(self, bucket: str, blob_path: str, content: str, content_type: str) -> None:
        try:
            blob = self._client.bucket(bucket).blob(blob_path)
            blob.upload_from_string(content, content_type=content_type)
        except Exception as e:
            raise GCSWriteError(f"Failed to write gs://{bucket}/{blob_path}: {e}") from e

    def _exists_sync(self, bucket: str, blob_path: str) -> bool:
        return self._client.bucket(bucket).blob(blob_path).exists()
```

> **Why sync client + `to_thread`**: Google's official async storage client (`gcloud-aio-storage`) is third-party and has fewer features. Wrapping the sync client is simpler and proven. The thread pool overhead is negligible for typical IO sizes.

### 4.5 `common/pubsub.py`

```python
from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.subscriber.message import Message
import asyncio
from typing import Awaitable, Callable
import threading

MessageHandler = Callable[[dict, dict], Awaitable[bool]]
# Returns: True -> ack, False -> nack
# Raises -> nack (logged at warning level)


class AsyncSubscriber:
    """
    Bridges Pub/Sub's threaded callback model to asyncio.

    Lifecycle:
      1. start()          - launches background StreamingPull
      2. process forever  - consumer tasks pull from internal queue
      3. shutdown()       - graceful drain on SIGTERM
    """

    def __init__(
        self,
        project_id: str,
        subscription: str,
        handler: MessageHandler,
        max_inflight: int = 20,
    ):
        self._project_id = project_id
        self._subscription = subscription
        self._handler = handler
        self._max_inflight = max_inflight

        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._streaming_future = None
        self._consumer_tasks: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()
        self._client: pubsub_v1.SubscriberClient | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._max_inflight)
        self._client = pubsub_v1.SubscriberClient()

        sub_path = self._client.subscription_path(self._project_id, self._subscription)

        # Pub/Sub callback runs in client's internal thread pool
        def _callback(message: Message) -> None:
            try:
                # Hand the message to the asyncio loop
                self._loop.call_soon_threadsafe(
                    self._queue.put_nowait, message
                )
            except asyncio.QueueFull:
                # Backpressure: nack so Pub/Sub redelivers later
                message.nack()

        flow = pubsub_v1.types.FlowControl(max_messages=self._max_inflight)
        self._streaming_future = self._client.subscribe(
            sub_path, callback=_callback, flow_control=flow
        )

        # Spawn consumer tasks
        for i in range(self._max_inflight):
            task = asyncio.create_task(self._consume_loop(worker_id=i))
            self._consumer_tasks.append(task)

    async def _consume_loop(self, worker_id: int) -> None:
        """Pull from queue, run handler, ack/nack based on outcome."""
        while not self._shutdown_event.is_set():
            try:
                message: Message = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            attrs = dict(message.attributes)
            try:
                payload = self._decode_payload(message.data)
            except Exception:
                # Bad message format: ack to drop (avoid poison pill)
                message.ack()
                continue

            try:
                ok = await self._handler(payload, attrs)
                if ok:
                    message.ack()
                else:
                    message.nack()
            except Exception:
                # Handler bug: nack so it can be retried (subject to delivery attempts limit on subscription)
                message.nack()

    @staticmethod
    def _decode_payload(data: bytes) -> dict:
        import json
        return json.loads(data.decode("utf-8"))

    async def run_forever(self) -> None:
        """Block until shutdown_event is set."""
        await self._shutdown_event.wait()

    async def shutdown(self, drain_timeout: float = 30.0) -> None:
        # Stop pulling new messages
        if self._streaming_future is not None:
            self._streaming_future.cancel()
            try:
                self._streaming_future.result(timeout=10)
            except Exception:
                pass

        # Signal consumers
        self._shutdown_event.set()

        # Wait for consumers to drain
        await asyncio.wait(self._consumer_tasks, timeout=drain_timeout)

        if self._client:
            self._client.close()
```

**Filter at subscription level (managed via Terraform / gcloud, not in code):**

```bash
# Pre-earnings subscription
gcloud pubsub subscriptions create earnings-events-pre-earnings-sub \
  --topic=earnings-events \
  --message-filter='attributes.event_type = "pre_earnings"' \
  --ack-deadline=600

# ECTS subscription
gcloud pubsub subscriptions create earnings-events-ects-sub \
  --topic=earnings-events \
  --message-filter='attributes.event_type = "ects"' \
  --ack-deadline=600
```

### 4.6 `common/claude_client.py`

```python
from anthropic import AsyncAnthropic, APIStatusError, RateLimitError, APIConnectionError
import asyncio
from common.exceptions import (
    ClaudeAPIError, ClaudeAPIRateLimitError,
    ClaudeAPITimeoutError, ClaudeAPIRetryExhaustedError,
)

class ClaudeClient:
    """
    Async Claude wrapper with exponential backoff retry.
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
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._model = model
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay

    async def complete(
        self,
        system: str,
        user_prompt: str,
        tools: list[dict] | None = None,
    ) -> str:
        """
        Returns concatenated text from response.
        For pre-earnings, pass tools=[web_search_tool_definition].
        """
        return await self._call_with_retry(system, user_prompt, tools)

    async def _call_with_retry(self, system, user_prompt, tools) -> str:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                kwargs = {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
                if tools:
                    kwargs["tools"] = tools

                resp = await self._client.messages.create(**kwargs)
                # Extract text blocks (skip tool_use blocks)
                texts = [
                    block.text for block in resp.content
                    if getattr(block, "type", None) == "text"
                ]
                return "\n".join(texts)

            except RateLimitError as e:
                last_exc = ClaudeAPIRateLimitError(str(e))
            except APIConnectionError as e:
                last_exc = ClaudeAPITimeoutError(str(e))
            except APIStatusError as e:
                if e.status_code >= 500:
                    last_exc = ClaudeAPIError(f"5xx: {e}")
                else:
                    # 4xx -> permanent, don't retry
                    raise ClaudeAPIError(f"4xx: {e}") from e
            except Exception as e:
                last_exc = ClaudeAPIError(f"Unexpected: {e}")

            # Exponential backoff: 2, 4, 8, 16, 32 seconds
            if attempt < self._max_retries - 1:
                delay = self._retry_base_delay * (2 ** attempt)
                await asyncio.sleep(delay)

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
```

### 4.7 `common/company_config.py`

```python
from pydantic import BaseModel, Field
from common.gcs_service import GCSService
from common.exceptions import CompanyConfigNotFoundError, CompanyConfigInvalidError, GCSObjectNotFound

class PollingConfig(BaseModel):
    start_offset_minutes: int = 30
    interval_minutes: int = 10
    max_attempts: int = 12

class SummaryTemplate(BaseModel):
    language: str = "en"
    sections: list[str]
    style_guidance: str = ""

class PreEarningsCompanyConfig(BaseModel):
    ticker: str
    company_name: str
    press_release_urls: list[str]
    financial_topics: list[str]
    polling: PollingConfig = Field(default_factory=PollingConfig)
    summary_template: SummaryTemplate
    prompt_extras: dict = Field(default_factory=dict)


class CompanyConfigLoader:
    def __init__(self, gcs: GCSService, bucket: str, prefix: str):
        self._gcs = gcs
        self._bucket = bucket
        self._prefix = prefix

    async def load_pre_earnings(self, ticker: str) -> PreEarningsCompanyConfig:
        path = f"{self._prefix}/{ticker}.json"
        try:
            raw = await self._gcs.read_json(self._bucket, path)
        except GCSObjectNotFound:
            raise CompanyConfigNotFoundError(f"No config for {ticker} at gs://{self._bucket}/{path}")
        try:
            return PreEarningsCompanyConfig(**raw)
        except Exception as e:
            raise CompanyConfigInvalidError(f"Invalid config for {ticker}: {e}") from e
```

---

## 5. Pre-earnings Module

### 5.1 `pre_earnings/models.py`

```python
from pydantic import BaseModel

class PreEarningsMessage(BaseModel):
    """Decoded from Pub/Sub message data."""
    ticker: str
    fiscal_year: str
    fiscal_quarter: str
    event_time_iso: str            # ISO8601, when the earnings call starts
```

### 5.2 `pre_earnings/worker.py`

The Pub/Sub message handler. **Critical: ack-on-receive, polling runs as background task.**

```python
import asyncio
from common.logging import ctx_ticker, ctx_workflow, ctx_message_id
from pre_earnings.models import PreEarningsMessage
from pre_earnings.monitor import PreEarningsMonitor

class PreEarningsWorker:
    def __init__(self, monitor: PreEarningsMonitor):
        self._monitor = monitor
        self._background_tasks: set[asyncio.Task] = set()

    async def handle(self, payload: dict, attrs: dict) -> bool:
        """
        Pub/Sub message handler.
        Returns True immediately (ack) and spawns background polling task.
        """
        ctx_workflow.set("pre_earnings")
        ctx_message_id.set(attrs.get("message_id", "?"))

        try:
            msg = PreEarningsMessage(**payload)
        except Exception:
            # Malformed message: ack to drop
            return True

        ctx_ticker.set(msg.ticker)

        # Fire-and-forget: polling continues after this returns
        task = asyncio.create_task(self._monitor.run(msg))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return True   # ack immediately
```

### 5.3 `pre_earnings/monitor.py`

The polling loop. Persists progress to GCS so Pod restart can recover.

```python
import asyncio
import logging
from datetime import datetime, timezone
from common.gcs_service import GCSService
from common.claude_client import ClaudeClient, web_search_tool
from common.company_config import CompanyConfigLoader, PreEarningsCompanyConfig
from common.exceptions import (
    PressReleaseNotFoundError, PollingExhaustedError,
    ClaudeAPIRetryExhaustedError, CompanyConfigNotFoundError,
)
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

        # Wait until polling start time (if event_time is in the future)
        start_offset_seconds = cfg.polling.start_offset_minutes * 60
        # ... compute when to start based on msg.event_time_iso ...

        for attempt in range(cfg.polling.max_attempts):
            log.info("polling_attempt", extra={"attempt": attempt + 1, "max": cfg.polling.max_attempts})
            try:
                summary = await self._try_fetch_and_summarize(msg, cfg)
                # Success!
                await self._write_output(msg, summary)
                log.info("press_release_captured", extra={"attempt": attempt + 1})
                return
            except PressReleaseNotFoundError:
                # Claude says no press release yet; sleep then retry
                log.info("press_release_not_yet")
            except ClaudeAPIRetryExhaustedError as e:
                # LLM service down; treat as soft fail, continue polling
                log.warning("claude_down_continuing", extra={"error": str(e)})

            if attempt < cfg.polling.max_attempts - 1:
                await asyncio.sleep(cfg.polling.interval_minutes * 60)

        log.warning("polling_exhausted", extra={"attempts": cfg.polling.max_attempts})

    async def _try_fetch_and_summarize(
        self, msg: PreEarningsMessage, cfg: PreEarningsCompanyConfig
    ) -> str:
        system, user = build_pre_earnings_prompt(msg, cfg)
        result = await self._claude.complete(
            system=system,
            user_prompt=user,
            tools=[web_search_tool(self._web_search_max_uses)],
        )

        # Sentinel: if Claude couldn't find a release, prompt instructs it to return exactly this
        if "PRESS_RELEASE_NOT_AVAILABLE" in result:
            raise PressReleaseNotFoundError()
        return result

    async def _write_output(self, msg: PreEarningsMessage, content: str) -> None:
        path = (
            f"{self._output_prefix}/company={msg.ticker}/"
            f"quarter={msg.fiscal_quarter}/fiscal={msg.fiscal_year}/"
            f"{msg.ticker}_FY_{msg.fiscal_quarter}_{msg.fiscal_year}.md"
        )
        await self._gcs.write_text(self._output_bucket, path, content)
```

### 5.4 `pre_earnings/prompt_builder.py`

```python
from pre_earnings.models import PreEarningsMessage
from common.company_config import PreEarningsCompanyConfig

def build_pre_earnings_prompt(
    msg: PreEarningsMessage, cfg: PreEarningsCompanyConfig,
) -> tuple[str, str]:
    system = f"""You are a financial analyst monitoring {cfg.company_name}'s ({cfg.ticker}) earnings press release.

You have access to a web_search tool. Use it ONLY on these official URLs:
{chr(10).join(f"  - {u}" for u in cfg.press_release_urls)}

Do NOT search third-party sites (Yahoo Finance, news outlets, social media).

If no official press release for {cfg.fiscal_quarter} {cfg.fiscal_year} is yet published on these URLs,
respond with EXACTLY this token and nothing else: PRESS_RELEASE_NOT_AVAILABLE
"""

    topics = "\n".join(f"  - {t}" for t in cfg.financial_topics)
    sections = "\n".join(f"  - {s}" for s in cfg.summary_template.sections)
    extras = cfg.prompt_extras.get("additional_context", "")

    user = f"""Find the official press release / earnings release for:
  Ticker: {msg.ticker}
  Fiscal Year: {msg.fiscal_year}
  Fiscal Quarter: {msg.fiscal_quarter}

Search the official URLs above. If found, produce a summary covering:
{topics}

Organize the summary into these sections:
{sections}

Style: {cfg.summary_template.style_guidance}
Language: {cfg.summary_template.language}

{extras}

Output the summary in Markdown.
If not found yet, respond with PRESS_RELEASE_NOT_AVAILABLE.
"""
    return system, user
```

### 5.5 `pre_earnings/main.py`

```python
import asyncio
import signal
from common.config import bootstrap_env, get_settings
from common.logging import setup_logging
from common.gcs_service import GCSService
from common.claude_client import ClaudeClient
from common.company_config import CompanyConfigLoader
from common.pubsub import AsyncSubscriber
from pre_earnings.monitor import PreEarningsMonitor
from pre_earnings.worker import PreEarningsWorker


async def amain() -> None:
    bootstrap_env()                          # MUST be first: load /app/.env
    settings = get_settings()
    setup_logging(settings.log_level)

    assert settings.app_mode == "pre_earnings"

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
    config_loader = CompanyConfigLoader(
        gcs,
        settings.gcs_bucket_company_config,
        settings.gcs_blob_prefix_pre_earnings_config,
    )
    monitor = PreEarningsMonitor(
        gcs, claude, config_loader,
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
    def _signal_handler():
        stop_event.set()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    await subscriber.start()
    await stop_event.wait()
    await subscriber.shutdown()


if __name__ == "__main__":
    asyncio.run(amain())
```

---

## 6. ECTS Module

### 6.1 `ects/models.py`

```python
from pydantic import BaseModel
import pandas as pd
from dataclasses import dataclass

class ECTSMessage(BaseModel):
    ticker: str
    fiscal_year: str
    fiscal_quarter: str

@dataclass
class ECTSProcessedData:
    """Output of data_processor; input to prompt_builder."""
    ticker: str
    fiscal_year: str
    fiscal_quarter: str
    transcript: str
    financial: pd.DataFrame
    segment: pd.DataFrame
    config: dict
```

### 6.2 `ects/data_processor.py` (interface; user fills implementation)

```python
"""
ects/data_processor.py

USER OWNS THE TRANSFORM LOGIC. This file defines:
  - The interface (load_and_process)
  - The orchestration (parallel GCS pulls, missing-data detection)
  - The error contract (MissingDataError, DataParseError)

The TODO sections are where you implement parquet -> domain DataFrame.
"""

import asyncio
import io
import logging
import pandas as pd
from common.gcs_service import GCSService
from common.exceptions import (
    MissingDataError, DataParseError, GCSObjectNotFound,
)
from ects.models import ECTSMessage, ECTSProcessedData

log = logging.getLogger(__name__)


class ECTSDataProcessor:
    """
    Pulls 4 sources from GCS in parallel for ONE company:
      - transcript (assumed parquet or text; you decide)
      - financial (parquet)
      - segment (parquet)
      - config (json)

    All 4 are required. Any missing -> MissingDataError (caller acks).
    """

    SOURCES = ("transcript", "financial", "segment", "config")

    def __init__(
        self,
        gcs: GCSService,
        bucket_transcript: str, prefix_transcript: str,
        bucket_financial: str, prefix_financial: str,
        bucket_segment: str, prefix_segment: str,
        bucket_config: str, prefix_config: str,
    ):
        self._gcs = gcs
        self._paths = {
            "transcript": (bucket_transcript, prefix_transcript),
            "financial": (bucket_financial, prefix_financial),
            "segment": (bucket_segment, prefix_segment),
            "config": (bucket_config, prefix_config),
        }

    async def load_and_process(self, msg: ECTSMessage) -> ECTSProcessedData:
        # Step 1: parallel pull
        raw = await self._pull_all(msg)

        # Step 2: parse each
        transcript = await self._parse_transcript(raw["transcript"])
        financial = await self._parse_financial(raw["financial"])
        segment = await self._parse_segment(raw["segment"])
        config = self._parse_config(raw["config"])

        # Step 3: USER TRANSFORM HOOK
        # TODO(user): apply any cross-source joins, filters, derived columns
        #             specific to your business logic here.
        financial, segment = self._user_transform(financial, segment, config)

        return ECTSProcessedData(
            ticker=msg.ticker,
            fiscal_year=msg.fiscal_year,
            fiscal_quarter=msg.fiscal_quarter,
            transcript=transcript,
            financial=financial,
            segment=segment,
            config=config,
        )

    # --- Pull stage ---
    async def _pull_all(self, msg: ECTSMessage) -> dict[str, bytes]:
        async def pull_one(source: str) -> tuple[str, bytes | None]:
            bucket, prefix = self._paths[source]
            blob_path = self._build_blob_path(prefix, source, msg)
            try:
                data = await self._gcs.read_bytes(bucket, blob_path)
                return source, data
            except GCSObjectNotFound:
                log.warning("ects_source_missing", extra={
                    "source": source, "bucket": bucket, "blob_path": blob_path,
                })
                return source, None

        results = await asyncio.gather(*[pull_one(s) for s in self.SOURCES])
        results_dict = dict(results)

        missing = [s for s, v in results_dict.items() if v is None]
        if missing:
            raise MissingDataError(msg.ticker, missing)

        return results_dict   # all non-None

    @staticmethod
    def _build_blob_path(prefix: str, source: str, msg: ECTSMessage) -> str:
        # TODO(user): adjust to actual Bloomberg path convention
        ext = "json" if source == "config" else "parquet"
        return (
            f"{prefix}/company={msg.ticker}/"
            f"quarter={msg.fiscal_quarter}/fiscal={msg.fiscal_year}/"
            f"{msg.ticker}.{ext}"
        )

    # --- Parse stage ---
    async def _parse_transcript(self, data: bytes) -> str:
        # TODO(user): if transcript is parquet, extract text column;
        #             if it's plain text, just decode.
        try:
            return await asyncio.to_thread(self._parse_transcript_sync, data)
        except Exception as e:
            raise DataParseError(f"transcript parse failed: {e}") from e

    def _parse_transcript_sync(self, data: bytes) -> str:
        # Placeholder: assume parquet with a 'text' column
        df = pd.read_parquet(io.BytesIO(data))
        return "\n".join(df["text"].astype(str).tolist())

    async def _parse_financial(self, data: bytes) -> pd.DataFrame:
        try:
            return await asyncio.to_thread(pd.read_parquet, io.BytesIO(data))
        except Exception as e:
            raise DataParseError(f"financial parse failed: {e}") from e

    async def _parse_segment(self, data: bytes) -> pd.DataFrame:
        try:
            return await asyncio.to_thread(pd.read_parquet, io.BytesIO(data))
        except Exception as e:
            raise DataParseError(f"segment parse failed: {e}") from e

    @staticmethod
    def _parse_config(data: bytes) -> dict:
        import json
        try:
            return json.loads(data.decode("utf-8"))
        except Exception as e:
            raise DataParseError(f"config parse failed: {e}") from e

    # --- USER TRANSFORM ---
    def _user_transform(
        self, financial: pd.DataFrame, segment: pd.DataFrame, config: dict,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        # TODO(user): your business-specific transformations
        return financial, segment
```

### 6.3 `ects/worker.py`

```python
import logging
from common.logging import ctx_ticker, ctx_workflow, ctx_message_id
from common.exceptions import (
    MissingDataError, DataParseError,
    ClaudeAPIRetryExhaustedError, GCSWriteError,
)
from common.gcs_service import GCSService
from common.claude_client import ClaudeClient
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
            return True   # ack malformed

        ctx_ticker.set(msg.ticker)

        try:
            processed = await self._processor.load_and_process(msg)
        except MissingDataError as e:
            log.error("ects_missing_data", extra={
                "ticker": e.ticker, "missing": e.missing_sources,
            })
            return True   # ack: data not arrived, no point retrying same msg
        except DataParseError as e:
            log.error("ects_data_parse_error", extra={"error": str(e)})
            return True   # ack: data corrupt, redelivery won't fix

        try:
            system, user = build_ects_prompt(processed)
            summary = await self._claude.complete(system=system, user_prompt=user)
        except ClaudeAPIRetryExhaustedError as e:
            log.error("ects_claude_exhausted", extra={"error": str(e)})
            return True   # ack: service down, don't redeliver

        try:
            await self._write_output(msg, summary)
        except GCSWriteError as e:
            log.error("ects_write_failed", extra={"error": str(e)})
            return False   # nack: transient, retry

        log.info("ects_summary_complete")
        return True

    async def _write_output(self, msg: ECTSMessage, content: str) -> None:
        path = (
            f"{self._output_prefix}/company={msg.ticker}/"
            f"quarter={msg.fiscal_quarter}/fiscal={msg.fiscal_year}/"
            f"{msg.ticker}_FY_{msg.fiscal_quarter}_{msg.fiscal_year}.md"
        )
        await self._gcs.write_text(self._output_bucket, path, content)
```

### 6.4 `ects/prompt_builder.py`

```python
from ects.models import ECTSProcessedData

def build_ects_prompt(data: ECTSProcessedData) -> tuple[str, str]:
    system = """You are a senior equity analyst writing a structured summary of an earnings call.
Use ONLY the data provided below. Do not speculate beyond what the transcript and tables state.
Cite specific numbers from the financial and segment tables when relevant.
"""

    fin_md = data.financial.to_markdown(index=False)
    seg_md = data.segment.to_markdown(index=False)

    user = f"""# Earnings Call Summary Request

**Company**: {data.ticker}
**Fiscal Year**: {data.fiscal_year}
**Fiscal Quarter**: {data.fiscal_quarter}

## Financial data
{fin_md}

## Segment data
{seg_md}

## Company-specific context
{data.config}

## Transcript
{data.transcript}

---

Produce a Markdown summary with these sections:
1. Headline numbers (revenue, EPS, margin, with YoY)
2. Segment performance highlights
3. Management commentary themes (growth drivers, headwinds)
4. Forward guidance
5. Notable Q&A points
"""
    return system, user
```

### 6.5 `ects/main.py`

Mirrors `pre_earnings/main.py` but wires `ECTSDataProcessor` + `ECTSWorker`. Same shutdown logic. Same `bootstrap_env()` call at the very top of `amain()` before anything else reads env vars. Skeleton:

```python
import asyncio, signal
from common.config import bootstrap_env, get_settings
from common.logging import setup_logging
# ... other imports ...

async def amain() -> None:
    bootstrap_env()                          # MUST be first
    settings = get_settings()
    setup_logging(settings.log_level)
    assert settings.app_mode == "ects"
    # ... wire ECTSDataProcessor, ECTSWorker, AsyncSubscriber, signal handlers ...

if __name__ == "__main__":
    asyncio.run(amain())
```


---

## 7. Deployment

### 7.1 Composition model: how ConfigMap + Secret become `/app/.env`

Two pieces of plain text get concatenated by an `initContainer` into a single `/app/.env`:

```
┌─────────────────────────────────┐
│ ConfigMap: earnings-intel-      │
│   shared-config                 │  ← non-sensitive, identical across both deployments
│   (mounted at /config-shared)   │     KEY=VALUE lines
└────────────────┬────────────────┘
                 │
┌────────────────┴────────────────┐
│ ConfigMap: earnings-intel-      │
│   {pre-earnings|ects}-config    │  ← per-workflow override (APP_MODE, subscription)
│   (mounted at /config-workflow) │
└────────────────┬────────────────┘
                 │
┌────────────────┴────────────────┐
│ Secret: earnings-intel-secret   │  ← sensitive, identical across both deployments
│   (mounted at /secret)          │     contains ANTHROPIC_API_KEY only (today)
└────────────────┬────────────────┘
                 │
                 ▼
        initContainer concatenates
                 │
                 ▼
┌─────────────────────────────────┐
│ emptyDir volume                 │
│ /app/.env  (in main container)  │
└─────────────────────────────────┘
```

**Why two ConfigMaps**: the shared one rarely changes; the per-workflow one only sets `APP_MODE` and `GCP_PUBSUB_SUBSCRIPTION`. Order in the concat matters — the workflow ConfigMap comes *after* the shared one so its keys win for any duplicate (this is also how `python-dotenv` resolves duplicates: last write wins within a file).

### 7.2 `deploy/Dockerfile`

```dockerfile
FROM python:3.11.11-bookworm AS base

WORKDIR /app

# System deps for any wheels that need to compile (rare with our pinned set,
# but kept for safety with grpcio / pyarrow on slim variants).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps using fully pinned requirements.txt (top-level + transitive).
# Done before COPY of source so that source changes don't bust this layer cache.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY pre_earnings/ ./pre_earnings/
COPY ects/ ./ects/

# Entrypoint dispatches by APP_MODE (which lives inside /app/.env,
# assembled by the initContainer at Pod start — not in shell env).
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER 1000:1000
ENTRYPOINT ["/entrypoint.sh"]
```

`deploy/entrypoint.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Sanity check: /app/.env must have been written by the initContainer.
if [ ! -f /app/.env ]; then
  echo "FATAL: /app/.env not found. Did the env-builder initContainer run?" >&2
  exit 1
fi

# We DON'T source /app/.env here. Python's load_dotenv() will read it.
# Read APP_MODE just enough to dispatch.
APP_MODE_VALUE=$(grep -E '^APP_MODE=' /app/.env | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")

case "${APP_MODE_VALUE}" in
  pre_earnings) exec python -m pre_earnings.main ;;
  ects)         exec python -m ects.main ;;
  *) echo "Unknown APP_MODE='${APP_MODE_VALUE}' in /app/.env"; exit 1 ;;
esac
```

### 7.3 `deploy/configmap-shared.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: earnings-intel-shared-config
  namespace: tmic
data:
  # The KEY here is the filename; its content is .env-format text.
  shared.env: |
    LOG_LEVEL=INFO
    ENVIRONMENT=production

    GCP_PROJECT_ID=my-gcp-project
    GCP_PUBSUB_TOPIC=earnings-events
    GCP_PUBSUB_MAX_INFLIGHT=20
    GCP_PUBSUB_ACK_DEADLINE_SECONDS=600

    GCS_PROJECT_ID=my-gcp-project
    GCS_CUSTOM_STORAGE_ENDPOINT=

    GCS_BUCKET_PRE_EARNINGS_OUTPUT=tmic-prod-pre-earnings
    GCS_BLOB_PREFIX_PRE_EARNINGS_OUTPUT=digwork/tmic/pre_earnings_summary
    GCS_BUCKET_ECTS_OUTPUT=tmic-prod-ects
    GCS_BLOB_PREFIX_ECTS_OUTPUT=digwork/tmic/ects_summary

    GCS_BUCKET_ECTS_TRANSCRIPT=tmic-prod-bbg-transcript
    GCS_BLOB_PREFIX_ECTS_TRANSCRIPT=bbg/transcript
    GCS_BUCKET_ECTS_FINANCIAL=tmic-prod-bbg-financial
    GCS_BLOB_PREFIX_ECTS_FINANCIAL=bbg/financial
    GCS_BUCKET_ECTS_SEGMENT=tmic-prod-bbg-segment
    GCS_BLOB_PREFIX_ECTS_SEGMENT=bbg/segment

    GCS_BUCKET_COMPANY_CONFIG=tmic-prod-company-config
    GCS_BLOB_PREFIX_PRE_EARNINGS_CONFIG=configs/pre_earnings
    GCS_BLOB_PREFIX_ECTS_CONFIG=configs/ects

    ANTHROPIC_MODEL=claude-opus-4-7
    ANTHROPIC_MODEL_MAX_TOKENS=8192
    ANTHROPIC_API_BASE_URL=https://api.anthropic.com
    ANTHROPIC_REQUEST_TIMEOUT_SECONDS=120
    ANTHROPIC_MAX_RETRIES=5
    ANTHROPIC_RETRY_BASE_DELAY_SECONDS=2
    ANTHROPIC_WEB_SEARCH_MAX_USES=10

    PRE_EARNINGS_DEFAULT_START_OFFSET_MINUTES=30
    PRE_EARNINGS_DEFAULT_POLL_INTERVAL_MINUTES=10
    PRE_EARNINGS_DEFAULT_MAX_ATTEMPTS=12
```

### 7.4 `deploy/configmap-pre-earnings.yaml` and `deploy/configmap-ects.yaml`

```yaml
# configmap-pre-earnings.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: earnings-intel-pre-earnings-config
  namespace: tmic
data:
  workflow.env: |
    APP_MODE=pre_earnings
    GCP_PUBSUB_SUBSCRIPTION=earnings-events-pre-earnings-sub
```

```yaml
# configmap-ects.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: earnings-intel-ects-config
  namespace: tmic
data:
  workflow.env: |
    APP_MODE=ects
    GCP_PUBSUB_SUBSCRIPTION=earnings-events-ects-sub
```

### 7.5 `deploy/secret.yaml` (you own this)

> **Important**: this file's *real* content (with the actual API key) is **never committed to Git**. Manage it with your secrets workflow (sealed-secrets, SOPS, manual `kubectl apply`, External Secrets Operator, etc.). The example below shows the shape only.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: earnings-intel-secret
  namespace: tmic
type: Opaque
stringData:
  # The KEY here is the filename. Content is .env-format text.
  secret.env: |
    ANTHROPIC_API_KEY=sk-ant-REPLACE_ME
```

`stringData` is auto-base64-encoded by K8s on apply (you write plain text in YAML, it stores base64). This is more readable than the `data:` field and avoids manual base64 encoding errors.

### 7.6 `deploy/service-account.yaml`

Workload Identity binding (KSA → GSA):

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: earnings-intel-sa
  namespace: tmic
  annotations:
    iam.gke.io/gcp-service-account: earnings-intel@my-gcp-project.iam.gserviceaccount.com
```

GSA needs:
- `roles/storage.objectViewer` on input buckets (transcript/financial/segment/config)
- `roles/storage.objectAdmin` on output buckets (pre_earnings_output, ects_output)
- `roles/pubsub.subscriber` on both subscriptions

### 7.7 `deploy/pre-earnings-deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pre-earnings
  namespace: tmic
spec:
  replicas: 2
  selector:
    matchLabels: { app: pre-earnings }
  template:
    metadata:
      labels: { app: pre-earnings }
      # Roll the pods when ConfigMap or Secret content changes.
      # Set these annotations in CI/CD to a hash of the source content.
      annotations:
        config-hash/shared: "REPLACE_AT_DEPLOY"
        config-hash/workflow: "REPLACE_AT_DEPLOY"
        config-hash/secret: "REPLACE_AT_DEPLOY"
    spec:
      serviceAccountName: earnings-intel-sa
      terminationGracePeriodSeconds: 60

      volumes:
        - name: env-shared
          configMap:
            name: earnings-intel-shared-config
        - name: env-workflow
          configMap:
            name: earnings-intel-pre-earnings-config
        - name: env-secret
          secret:
            secretName: earnings-intel-secret
        - name: app-env
          emptyDir: {}      # writable home for the assembled /app/.env

      initContainers:
        - name: env-builder
          image: busybox:1.36
          # Concat order = precedence (later wins for duplicate keys, just like dotenv):
          #   1. shared (defaults + non-sensitive)
          #   2. workflow (overrides APP_MODE, subscription)
          #   3. secret  (sensitive — never duplicate, just appended)
          # Trailing newlines between blocks ensure dotenv parsing is clean.
          command:
            - sh
            - -c
            - |
              set -e
              {
                cat /config-shared/shared.env
                printf "\n"
                cat /config-workflow/workflow.env
                printf "\n"
                cat /secret/secret.env
                printf "\n"
              } > /app-env/.env
              chmod 0400 /app-env/.env
          volumeMounts:
            - { name: env-shared,   mountPath: /config-shared,   readOnly: true }
            - { name: env-workflow, mountPath: /config-workflow, readOnly: true }
            - { name: env-secret,   mountPath: /secret,          readOnly: true }
            - { name: app-env,      mountPath: /app-env }

      containers:
        - name: app
          image: REGION-docker.pkg.dev/PROJECT/REPO/earnings-intel:VERSION
          workingDir: /app
          # We deliberately do NOT use envFrom or env. All settings come from /app/.env.
          # The only exception is GOOGLE_CLOUD_PROJECT, which some Google client libs read
          # before our load_dotenv() runs (e.g. during library import). Set it explicitly:
          env:
            - name: GOOGLE_CLOUD_PROJECT
              value: "my-gcp-project"
          volumeMounts:
            - name: app-env
              mountPath: /app/.env
              subPath: .env
              readOnly: true
          resources:
            requests: { cpu: "200m", memory: "512Mi" }
            limits:   { cpu: "2",    memory: "2Gi" }
          readinessProbe:
            exec: { command: ["/bin/sh", "-c", "test -f /tmp/ready"] }
            periodSeconds: 10
          livenessProbe:
            exec: { command: ["/bin/sh", "-c", "test -f /tmp/alive"] }
            periodSeconds: 30
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: pre-earnings-hpa
  namespace: tmic
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: pre-earnings
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: External
      external:
        metric:
          name: pubsub.googleapis.com|subscription|num_undelivered_messages
          selector:
            matchLabels:
              resource.labels.subscription_id: earnings-events-pre-earnings-sub
        target:
          type: AverageValue
          averageValue: "5"
```

`deploy/ects-deployment.yaml` is identical structure with:
- `APP_MODE=ects` (via `earnings-intel-ects-config` ConfigMap)
- `GCP_PUBSUB_SUBSCRIPTION=earnings-events-ects-sub`
- Possibly larger `resources.limits.memory` for parquet parsing
- HPA selector pointing at the ects subscription

### 7.8 Why CI/CD must annotate config hashes

K8s does **not** restart Pods when a ConfigMap or Secret changes. Since `/app/.env` is built once at Pod startup, a config update only reaches the runtime if the Pod restarts. Standard fix: in CI/CD, compute a hash of each ConfigMap/Secret YAML and inject it into the Deployment's `spec.template.metadata.annotations`. Any change to a hash makes K8s see a different Pod template and triggers a rolling update. Example with kubectl:

```bash
SHARED_HASH=$(sha256sum deploy/configmap-shared.yaml | cut -c1-12)
WORKFLOW_HASH=$(sha256sum deploy/configmap-pre-earnings.yaml | cut -c1-12)
SECRET_HASH=$(sha256sum /tmp/rendered-secret.yaml | cut -c1-12)

kubectl set annotation deployment/pre-earnings -n tmic \
  config-hash/shared="${SHARED_HASH}" \
  config-hash/workflow="${WORKFLOW_HASH}" \
  config-hash/secret="${SECRET_HASH}" \
  --overwrite
```

> **Health probes**: app touches `/tmp/alive` periodically and `/tmp/ready` after subscriber starts. Detail in implementation.

### 7.9 Local development

Skip everything in this section for local dev. Just:

```bash
cp .env.example .env
# fill in real ANTHROPIC_API_KEY etc.
python -m pre_earnings.main
```

`load_dotenv()` finds `.env` in the repo root automatically because of CWD search.

---

## 8. Exception Handling Matrix

| Exception | Where raised | Handler action | Pub/Sub action | Log level |
|-----------|--------------|---------------|---------------|-----------|
| `MessageDecodeError` | pubsub.py | drop | ack | ERROR |
| Pydantic validation on payload | worker | drop | ack | ERROR |
| `CompanyConfigNotFoundError` | monitor | log + return | ack (pre-earnings already acked) | ERROR |
| `PressReleaseNotFoundError` | monitor | sleep + retry | n/a (already acked) | INFO |
| `PollingExhaustedError` (logical) | monitor | log + audit | n/a | WARNING |
| `ClaudeAPIRetryExhaustedError` (pre-earnings) | monitor | log + continue polling | n/a | WARNING |
| `ClaudeAPIRetryExhaustedError` (ects) | worker | log + ack | ack | ERROR |
| `MissingDataError` (ects) | data_processor | log + audit + ack | ack | ERROR |
| `DataParseError` (ects) | data_processor | log + alert + ack | ack | ERROR |
| `GCSWriteError` (ects output) | worker | log + nack | nack (retry) | ERROR |
| Unexpected exception | anywhere | log with stack | nack | ERROR |

---

## 9. Test Strategy

### 9.1 Unit tests

| Module | Coverage |
|--------|----------|
| `gcs_service` | mocked `storage.Client`, exercise read/write/exists, NotFound mapping |
| `pubsub` | inject fake subscriber client, verify thread→asyncio bridge with `loop.call_soon_threadsafe` semantics, ack/nack on handler outcome |
| `claude_client` | mock `AsyncAnthropic`, verify retry count, exponential backoff, exhaustion exception |
| `company_config` | valid/invalid/missing JSON cases |
| `pre_earnings.worker` | verify ack-on-receive (handler returns True before polling completes) |
| `pre_earnings.monitor` | verify retry-on-not-found, success path writes correct GCS path, polling exhaustion |
| `ects.data_processor` | parallel pull, MissingDataError if any source missing, DataParseError on bad parquet |
| `ects.worker` | each exception class → correct ack/nack |

Use:
- `pytest` + `pytest-asyncio`
- `pytest-mock` for sync mocks
- `unittest.mock.AsyncMock` for async
- Fakes (not just mocks) for GCS — implement an in-memory `FakeGCSService` that satisfies the same interface

### 9.2 Integration tests

- Use Pub/Sub emulator (`docker run google/cloud-sdk gcloud beta emulators pubsub start`)
- Use fake GCS (in-memory or `fake-gcs-server`)
- Mock Claude API via `respx` (for the underlying HTTPX client)
- Test cases:
  - End-to-end pre-earnings: publish msg → polling starts → mocked Claude returns release on 2nd attempt → GCS contains expected blob
  - End-to-end ects: publish msg → fake GCS has all 4 sources → mocked Claude returns summary → GCS contains output
  - Missing data path: publish ects msg → fake GCS missing transcript → ack happens, no output written

### 9.3 Conftest fixtures

```python
# tests/conftest.py
import pytest
import pytest_asyncio

@pytest.fixture
def fake_settings(monkeypatch):
    """Set all required env vars to fake values."""
    ...

@pytest_asyncio.fixture
async def fake_gcs():
    """In-memory GCS that satisfies GCSService interface."""
    ...

@pytest.fixture
def fake_claude_client(mocker):
    """AsyncMock returning canned responses; configurable per test."""
    ...
```

---

## 10. Operational Checklist

Before going to production with N companies:

- [ ] Pub/Sub topic and both subscriptions created (with filters)
- [ ] Dead letter topic + max delivery attempts set on each subscription (e.g. 5 attempts → DLT)
- [ ] All 5 GCS buckets created with correct IAM
- [ ] Workload Identity binding verified (`gcloud iam service-accounts get-iam-policy`)
- [ ] `earnings-intel-secret` K8s Secret applied to `tmic` namespace with real `ANTHROPIC_API_KEY`
- [ ] `earnings-intel-shared-config` and per-workflow ConfigMaps applied
- [ ] CI/CD pipeline computes config hashes and annotates Deployment to trigger rollout on change
- [ ] `.env` is in `.gitignore`
- [ ] Cloud Logging filter for structured fields (`jsonPayload.workflow`, `jsonPayload.ticker`)
- [ ] Cloud Monitoring alerts:
  - Pub/Sub `num_undelivered_messages` > threshold
  - Custom metric: ECTS `claude_exhausted` rate > threshold
  - Custom metric: Pre-earnings `polling_exhausted` rate > threshold
- [ ] Per-company configs uploaded to GCS for all N companies
- [ ] Load test: simulate N concurrent earnings events to verify HPA + Anthropic rate limits hold

---

## 11. Open items for future iteration

1. **Anthropic rate limit budgeting**: at N=200+ companies on a single earnings day, you'll need a token-bucket throttle in `ClaudeClient` (or a global limiter via Redis). Defer until you hit a real limit.
2. **Polling state persistence**: current design loses polling progress on Pod restart. If that's unacceptable, add a Firestore checkpoint (write `last_attempt` per ticker; on startup, scan for in-flight events and resume).
3. **Per-company config schema versioning**: when you change `PreEarningsCompanyConfig`, old GCS configs break. Add `schema_version` field early.
4. **ects config schema**: not designed yet — when you fill `data_processor.py`'s `_user_transform`, define the `config` JSON shape and add a Pydantic model parallel to `PreEarningsCompanyConfig`.
