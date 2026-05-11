"""Deterministic + LLM verdict merge (``docs/DESIGN.md`` §11.8; issue #61)."""

from __future__ import annotations

from reviewgate.core.schemas import Reviewability

_ORDER: dict[Reviewability, int] = {"PASS": 0, "WARN": 1, "FAIL": 2}


def max_severity(a: Reviewability, b: Reviewability) -> Reviewability:
    """Return the stricter of two reviewability levels."""

    return a if _ORDER[a] >= _ORDER[b] else b


def merge_final_reviewability(
    deterministic_baseline: Reviewability,
    llm_verdict: Reviewability | None,
) -> Reviewability:
    """§11.8 ``final_reviewability = max_severity(deterministic, llm)``.

    When the LLM stage is skipped or unparsable, ``llm_verdict`` is ``None``
    and the baseline is returned unchanged.

    Args:
        deterministic_baseline: Engine-owned PASS/WARN/FAIL.
        llm_verdict: Hosted LLM verdict when present.

    Returns:
        Merged verdict for checks, labels, and headers.
    """

    if llm_verdict is None:
        return deterministic_baseline
    return max_severity(deterministic_baseline, llm_verdict)
