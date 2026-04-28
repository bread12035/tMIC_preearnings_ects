"""Build Claude prompts for the pre-earnings workflow.

Stock Titan news is the PRIMARY web_search source. The per-company IR URLs
and ``financial_topics`` (from the GCS config) are kept as a FALLBACK — when
absent, ticker + company_name + fiscal year/quarter alone are enough.
"""

from __future__ import annotations

from common.company_config import PreEarningsCompanyConfig
from common.prompt_loader import render
from pre_earnings.models import PreEarningsMessage

DEFAULT_TOPICS: tuple[str, ...] = (
    "Revenue (with YoY %)",
    "EPS, diluted (with YoY %)",
    "Gross / operating / net margin",
    "Segment performance highlights",
    "Forward guidance",
)

DEFAULT_SECTIONS: tuple[str, ...] = (
    "Headline numbers",
    "Segment performance",
    "Margin and profitability",
    "Forward guidance",
)

DEFAULT_STYLE = (
    "Concise. Use bullet points. Include YoY % deltas where the source "
    "provides them. Quote management commentary verbatim only when the "
    "original phrasing matters (<=15 words)."
)


def _bullets(items: list[str] | tuple[str, ...]) -> str:
    return "\n".join(f"  - {x}" for x in items)


def _fallback_system_section(cfg: PreEarningsCompanyConfig) -> str:
    if not cfg.press_release_urls:
        return ""
    urls = _bullets(cfg.press_release_urls)
    return (
        "\nFALLBACK SOURCES (only if Stock Titan does not have the release yet) "
        "— official IR URLs:\n"
        f"{urls}\n"
    )


def _fallback_user_hint(cfg: PreEarningsCompanyConfig) -> str:
    if not cfg.press_release_urls:
        return ""
    return " If Stock Titan has nothing yet, fall back to the official IR URLs listed in the system prompt."


def build_pre_earnings_prompt(
    msg: PreEarningsMessage,
    cfg: PreEarningsCompanyConfig,
    *,
    stocktitan_news_url: str,
    system_template_path: str,
    user_template_path: str,
) -> tuple[str, str]:
    topics = list(cfg.financial_topics) or list(DEFAULT_TOPICS)
    sections = list(cfg.summary_template.sections) or list(DEFAULT_SECTIONS)
    style = cfg.summary_template.style_guidance or DEFAULT_STYLE
    language = cfg.summary_template.language or "en"
    extras = cfg.prompt_extras.get("additional_context", "") if cfg.prompt_extras else ""
    additional_context = f"\n{extras}\n" if extras else ""

    system = render(
        system_template_path,
        company_name=cfg.company_name,
        ticker=cfg.ticker,
        fiscal_year=msg.fiscal_year,
        fiscal_quarter=msg.fiscal_quarter,
        stocktitan_news_url=stocktitan_news_url,
        fallback_section=_fallback_system_section(cfg),
    )

    user = render(
        user_template_path,
        ticker=cfg.ticker,
        company_name=cfg.company_name,
        fiscal_year=msg.fiscal_year,
        fiscal_quarter=msg.fiscal_quarter,
        stocktitan_news_url=stocktitan_news_url,
        fallback_user_hint=_fallback_user_hint(cfg),
        topics_block=_bullets(topics),
        sections_block=_bullets(sections),
        style_guidance=style,
        language=language,
        additional_context=additional_context,
    )

    return system, user
