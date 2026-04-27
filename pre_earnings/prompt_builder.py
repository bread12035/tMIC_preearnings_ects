"""Build Claude prompts for the pre-earnings workflow."""

from __future__ import annotations

from common.company_config import PreEarningsCompanyConfig
from pre_earnings.models import PreEarningsMessage


def build_pre_earnings_prompt(
    msg: PreEarningsMessage,
    cfg: PreEarningsCompanyConfig,
) -> tuple[str, str]:
    urls_block = "\n".join(f"  - {u}" for u in cfg.press_release_urls)
    system = f"""You are a financial analyst monitoring {cfg.company_name}'s ({cfg.ticker}) earnings press release.

You have access to a web_search tool. Use it ONLY on these official URLs:
{urls_block}

Do NOT search third-party sites (Yahoo Finance, news outlets, social media).

If no official press release for {msg.fiscal_quarter} {msg.fiscal_year} is yet published on these URLs,
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
