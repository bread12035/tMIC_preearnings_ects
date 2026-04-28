"""Load prompt templates from disk and render them via str.format().

Templates live under the directory configured via PROMPT_DIR (defaults to
`prompts/` relative to the working directory). At Pod runtime the directory
is mounted from a ConfigMap; locally and in tests it lives in the repo.

Templates use ``{name}`` placeholders. Any literal braces in the template
content must be doubled (``{{`` / ``}}``) per ``str.format`` rules.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


class PromptTemplateError(RuntimeError):
    """Raised when a prompt template can't be loaded or rendered."""


@lru_cache(maxsize=64)
def _read(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise PromptTemplateError(f"prompt template not found: {path}")
    return p.read_text(encoding="utf-8")


def render(path: str, /, **fields: object) -> str:
    """Read the template at ``path`` and substitute ``{name}`` placeholders."""
    text = _read(path)
    try:
        return text.format(**fields)
    except KeyError as e:
        raise PromptTemplateError(
            f"missing placeholder {e.args[0]!r} for template {path}"
        ) from e


def reset_cache() -> None:
    """Clear the read cache (used in tests when a template file changes)."""
    _read.cache_clear()
