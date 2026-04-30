"""
ects/data_processor.py

USER OWNS THE TRANSFORM LOGIC. This file defines:
  - The interface (load_and_process)
  - The orchestration (sequential GCS reads, missing-data detection)
  - The error contract (MissingDataError, DataParseError)

The TODO sections are where you implement parquet -> domain DataFrame.
"""

from __future__ import annotations

import io
import json
import logging

import pandas as pd

from common.exceptions import (
    DataParseError,
    GCSObjectNotFound,
    MissingDataError,
)
from common.gcs_service import GCSService
from ects.models import ECTSMessage, ECTSProcessedData

log = logging.getLogger(__name__)


class ECTSDataProcessor:
    """
    Pulls 4 sources from GCS sequentially for ONE company:
      - transcript (parquet)
      - financial (parquet)
      - segment (parquet)
      - config (json)

    All 4 are required. Any missing -> MissingDataError (caller acks).
    Sequential GCS reads — total latency is negligible vs Claude call;
    no parallelism needed (see SDD §6.2 note on future ThreadPoolExecutor).
    """

    SOURCES = ("transcript", "financial", "segment", "config")

    def __init__(
        self,
        gcs: GCSService,
        bucket_transcript: str,
        prefix_transcript: str,
        bucket_financial: str,
        prefix_financial: str,
        bucket_segment: str,
        prefix_segment: str,
        bucket_config: str,
        prefix_config: str,
    ):
        self._gcs = gcs
        self._paths = {
            "transcript": (bucket_transcript, prefix_transcript),
            "financial": (bucket_financial, prefix_financial),
            "segment": (bucket_segment, prefix_segment),
            "config": (bucket_config, prefix_config),
        }

    def load_and_process(self, msg: ECTSMessage) -> ECTSProcessedData:
        # Step 1: sequential pull
        raw: dict[str, bytes] = {}
        missing: list[str] = []
        for source in self.SOURCES:
            bucket, prefix = self._paths[source]
            blob_path = self._build_blob_path(prefix, source, msg)
            try:
                raw[source] = self._gcs.read_bytes(bucket, blob_path)
            except GCSObjectNotFound:
                log.warning(
                    "ects_source_missing",
                    extra={
                        "source": source,
                        "bucket": bucket,
                        "blob_path": blob_path,
                    },
                )
                missing.append(source)

        if missing:
            raise MissingDataError(msg.ticker, missing)

        # Step 2: parse each
        transcript = self._parse_transcript(raw["transcript"])
        financial = self._parse_financial(raw["financial"])
        segment = self._parse_segment(raw["segment"])
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
    def _parse_transcript(self, data: bytes) -> str:
        # TODO(user): if transcript is parquet, extract text column;
        #             if it's plain text, just decode.
        try:
            df = pd.read_parquet(io.BytesIO(data))
            if "text" not in df.columns:
                raise DataParseError(
                    f"transcript parquet missing 'text' column; cols={list(df.columns)}"
                )
            return "\n".join(df["text"].astype(str).tolist())
        except DataParseError:
            raise
        except Exception as e:
            raise DataParseError(f"transcript parse failed: {e}") from e

    def _parse_financial(self, data: bytes) -> pd.DataFrame:
        try:
            return pd.read_parquet(io.BytesIO(data))
        except Exception as e:
            raise DataParseError(f"financial parse failed: {e}") from e

    def _parse_segment(self, data: bytes) -> pd.DataFrame:
        try:
            return pd.read_parquet(io.BytesIO(data))
        except Exception as e:
            raise DataParseError(f"segment parse failed: {e}") from e

    @staticmethod
    def _parse_config(data: bytes) -> dict:
        try:
            return json.loads(data.decode("utf-8"))
        except Exception as e:
            raise DataParseError(f"config parse failed: {e}") from e

    # --- USER TRANSFORM ---
    def _user_transform(
        self,
        financial: pd.DataFrame,
        segment: pd.DataFrame,
        config: dict,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        # TODO(user): your business-specific transformations
        return financial, segment
