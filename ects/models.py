"""Pydantic / dataclass models for the ECTS workflow."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from pydantic import BaseModel


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
