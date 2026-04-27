"""Custom exception hierarchy for the earnings-intel service."""

from __future__ import annotations


class EarningsIntelError(Exception):
    """Base class for all custom exceptions."""


# --- GCS ---
class GCSError(EarningsIntelError):
    pass


class GCSObjectNotFound(GCSError):
    pass


class GCSWriteError(GCSError):
    pass


# --- Pub/Sub ---
class PubSubError(EarningsIntelError):
    pass


class MessageDecodeError(PubSubError):
    pass


# --- Claude ---
class ClaudeAPIError(EarningsIntelError):
    pass


class ClaudeAPIRateLimitError(ClaudeAPIError):
    pass


class ClaudeAPITimeoutError(ClaudeAPIError):
    pass


class ClaudeAPIRetryExhaustedError(ClaudeAPIError):
    """Raised after all retries fail; treated as 'service down', ack the message."""


# --- Pre-earnings ---
class PreEarningsError(EarningsIntelError):
    pass


class PressReleaseNotFoundError(PreEarningsError):
    """Claude returned 'no press release available yet'. Caller should retry on next poll."""


class PollingExhaustedError(PreEarningsError):
    """All polling attempts done, still no press release. Log + audit, ack."""


# --- ECTS ---
class ECTSError(EarningsIntelError):
    pass


class MissingDataError(ECTSError):
    """Required GCS source(s) not found. Ack + audit log."""

    def __init__(self, ticker: str, missing_sources: list[str]):
        super().__init__(f"Missing data for {ticker}: {missing_sources}")
        self.ticker = ticker
        self.missing_sources = missing_sources


class DataParseError(ECTSError):
    """Source file exists but parquet/JSON parse failed. Ack + alert."""


# --- Config ---
class ConfigError(EarningsIntelError):
    pass


class CompanyConfigNotFoundError(ConfigError):
    pass


class CompanyConfigInvalidError(ConfigError):
    pass
