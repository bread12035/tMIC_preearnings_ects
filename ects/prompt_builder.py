"""Build Claude prompts for the ECTS workflow.

Two modes:
  * Data-mode (default): GCS-loaded transcript + financial/segment parquet
    are inlined into the prompt.
  * Web-search mode (``web_search_flag=true``): ticker / fiscal year / quarter
    only — Claude pulls financial results from Stock Titan news and the
    transcript from Motley Fool via the web_search tool, then composes the
    summary from a configurable inner template.
"""

from __future__ import annotations

from common.prompt_loader import render
from ects.models import ECTSMessage, ECTSProcessedData


def build_ects_prompt(
    data: ECTSProcessedData,
    *,
    system_template_path: str,
    user_template_path: str,
) -> tuple[str, str]:
    """Data-mode prompt: transcript + financial/segment tables already in hand."""
    fin_md = data.financial.to_markdown(index=False)
    seg_md = data.segment.to_markdown(index=False)

    system = render(system_template_path)
    user = render(
        user_template_path,
        ticker=data.ticker,
        fiscal_year=data.fiscal_year,
        fiscal_quarter=data.fiscal_quarter,
        financial_md=fin_md,
        segment_md=seg_md,
        company_config=data.config,
        transcript=data.transcript,
    )
    return system, user


def build_ects_web_search_prompt(
    msg: ECTSMessage,
    *,
    company_name: str | None,
    stocktitan_news_url: str,
    motley_fool_url: str,
    system_template_path: str,
    user_template_path: str,
    template_path: str,
) -> tuple[str, str]:
    """Web-search mode: Stock Titan (financial) + Motley Fool (transcript)."""
    inner_template = render(
        template_path,
        ticker=msg.ticker,
        fiscal_year=msg.fiscal_year,
        fiscal_quarter=msg.fiscal_quarter,
    )

    system = render(
        system_template_path,
        ticker=msg.ticker,
        company_name=company_name or msg.ticker,
        fiscal_year=msg.fiscal_year,
        fiscal_quarter=msg.fiscal_quarter,
        stocktitan_news_url=stocktitan_news_url,
        motley_fool_url=motley_fool_url,
    )
    user = render(
        user_template_path,
        ticker=msg.ticker,
        company_name=company_name or msg.ticker,
        fiscal_year=msg.fiscal_year,
        fiscal_quarter=msg.fiscal_quarter,
        stocktitan_news_url=stocktitan_news_url,
        motley_fool_url=motley_fool_url,
        template=inner_template,
    )
    return system, user
