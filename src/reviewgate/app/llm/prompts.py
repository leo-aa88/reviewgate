"""Prompt asset loader (``docs/DESIGN.md`` §11.6; issue #58)."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Final

_PACKAGE: Final[str] = "reviewgate.app.llm"
_PROMPT_NAME: Final[str] = "reviewability_v1.txt"


@lru_cache(maxsize=1)
def load_reviewability_v1_prompt() -> str:
    """Return the UTF-8 text of the bundled §11.6 reviewability prompt."""

    return resources.files(_PACKAGE).joinpath(_PROMPT_NAME).read_text(encoding="utf-8")
