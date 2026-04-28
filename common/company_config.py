"""Per-company configuration loaded from GCS."""

from __future__ import annotations

from pydantic import BaseModel, Field

from common.exceptions import (
    CompanyConfigInvalidError,
    CompanyConfigNotFoundError,
    GCSObjectNotFound,
)
from common.gcs_service import GCSService


class PollingConfig(BaseModel):
    start_offset_minutes: int = 30
    interval_minutes: int = 10
    max_attempts: int = 12


class SummaryTemplate(BaseModel):
    language: str = "en"
    sections: list[str] = Field(default_factory=list)
    style_guidance: str = ""


class PreEarningsCompanyConfig(BaseModel):
    """
    Per-company configuration. Only ``ticker`` and ``company_name`` are
    required; everything else is optional so a bare config (no IR URL,
    no topics) can still drive a Stock Titan-only lookup using just
    ticker + company_name + fiscal year/quarter.
    """

    ticker: str
    company_name: str
    press_release_urls: list[str] = Field(default_factory=list)
    financial_topics: list[str] = Field(default_factory=list)
    polling: PollingConfig = Field(default_factory=PollingConfig)
    summary_template: SummaryTemplate = Field(default_factory=SummaryTemplate)
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
        except GCSObjectNotFound as e:
            raise CompanyConfigNotFoundError(
                f"No config for {ticker} at gs://{self._bucket}/{path}"
            ) from e
        try:
            return PreEarningsCompanyConfig(**raw)
        except Exception as e:
            raise CompanyConfigInvalidError(
                f"Invalid config for {ticker}: {e}"
            ) from e
