"""Baseline reviewability aggregation (docs/DESIGN.md \u00a710.13).

The deterministic engine emits :class:`EngineWarning` records as it inspects a
PR. This module reduces that collection to a single PASS/WARN/FAIL verdict
using only the ``severity`` field, exactly as documented in \u00a710.13.

The function is pure (no I/O, no global state, no randomness) so it can be
called from the open-source CLI, the GitHub Action, and the hosted App
worker without behavioural drift. The hosted LLM layer may later escalate
this baseline (\u00a711.8), but never downgrade it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from .schemas import EngineWarning, Reviewability

# DESIGN.md \u00a710.13 decision ladder. Named so the spec mapping lives in one
# place and so static checkers can flag any drift between code and doc.
_HIGH_COUNT_FOR_FAIL: Final[int] = 2
_HIGH_COUNT_FOR_WARN: Final[int] = 1
_MEDIUM_COUNT_FOR_FAIL_WITH_ONE_HIGH: Final[int] = 1
_MEDIUM_COUNT_FOR_WARN_ALONE: Final[int] = 2


def baseline_reviewability(warnings: Iterable[EngineWarning]) -> Reviewability:
    """Compute PASS/WARN/FAIL from warning severities (\u00a710.13).

    The result depends only on the counts of ``high`` and ``medium``
    severities; ``low`` warnings are reviewer-burden hints and never
    contribute to the baseline verdict. The function is order-independent
    and idempotent: calling it twice on the same input yields the same
    result without mutating ``warnings``.

    Args:
        warnings: Iterable of :class:`EngineWarning` produced by the
            deterministic heuristics (\u00a710). Generators are accepted; the
            iterable is consumed exactly once.

    Returns:
        Baseline reviewability verdict per the \u00a710.13 ladder:

        * ``"FAIL"`` if there are at least two ``high``-severity warnings,
          or at least one ``high`` paired with at least one ``medium``.
        * ``"WARN"`` if there is exactly one ``high`` (with no ``medium``)
          or at least two ``medium`` (with no ``high``).
        * ``"PASS"`` otherwise (including the empty input).
    """
    high = 0
    medium = 0
    for warning in warnings:
        if warning.severity == "high":
            high += 1
        elif warning.severity == "medium":
            medium += 1

    if high >= _HIGH_COUNT_FOR_FAIL:
        return "FAIL"
    if (
        high >= _HIGH_COUNT_FOR_WARN
        and medium >= _MEDIUM_COUNT_FOR_FAIL_WITH_ONE_HIGH
    ):
        return "FAIL"
    if high >= _HIGH_COUNT_FOR_WARN or medium >= _MEDIUM_COUNT_FOR_WARN_ALONE:
        return "WARN"
    return "PASS"


__all__ = ["baseline_reviewability"]
