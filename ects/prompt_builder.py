"""Build Claude prompts for the ECTS workflow."""

from __future__ import annotations

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
