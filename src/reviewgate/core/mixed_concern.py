"""Mixed-concern detection (docs/DESIGN.md \u00a710.11).

\u00a710.11 explicitly warns against firing on plain category diversity:
"a good feature PR may touch source, tests, docs, and config" and
"do not overclaim semantic scope drift until linked issue and PR body
comparison are stronger."

This module implements the **conservative** subset of \u00a710.11's
suspicious-combination list -- one rule, one code, one severity:

    If a PR touches 3 or more distinct named risk concerns from the
    set {auth, billing, infra, migration}, emit a single warning.

That single rule covers two of the spec's six suspicious examples:

* ``billing + auth + infra``
* ``migration + workflow + unrelated UI refactor``
  (the ``workflow`` part counts as ``infra``).

The other four examples (``dependency update + behavioral feature
change``, ``large refactor + business logic change``,
``formatting-only churn + functional change``,
``auth + unrelated docs rewrite + dependency bump``) require either
semantic diff analysis (AST diff for "formatting only", linked-issue
comparison for "behavioural feature change") or noisy heuristics that
the spec explicitly tells us to avoid until those signals exist.

Pure: no I/O, no GitHub or LLM dependency. The heuristic operates on
the categoriser output (#9) and is independent of the PR body.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from .schemas import EngineWarning, FileCategory, FileCategoryRow, WarningSeverity

WARN_CODE_MIXED_CONCERN: Final[str] = "mixed_concern_clusters"
"""Stable \u00a710.12 code emitted by :func:`mixed_concern_warning`."""

# The four \u00a710.5 categories the categoriser derives from the \u00a710.6
# risky-path subsets. Touching three or more of these in one PR is the
# spec's strongest signal of a "suspicious unrelated cluster" without
# needing semantic scope analysis.
_RISK_CATEGORIES: Final[frozenset[FileCategory]] = frozenset(
    {"auth", "billing", "infra", "migration"},
)

_MIN_RISK_CATEGORIES_FOR_WARNING: Final[int] = 3
"""How many distinct risk categories must co-occur to fire (\u00a710.11)."""

_SEVERITY: Final[WarningSeverity] = "medium"


def _risk_categories_touched(
    file_categories: Iterable[FileCategoryRow],
) -> set[FileCategory]:
    """Union of :data:`_RISK_CATEGORIES` carried by any row.

    A row may carry multiple categories (the \u00a710.5 example
    ``["source", "auth"]`` is exactly the case we care about); only
    risk-category labels contribute.
    """

    seen: set[FileCategory] = set()
    for row in file_categories:
        for cat in row.categories:
            if cat in _RISK_CATEGORIES:
                seen.add(cat)
    return seen


def mixed_concern_warning(
    file_categories: Iterable[FileCategoryRow],
) -> EngineWarning | None:
    """Emit a \u00a710.11 mixed-concern warning, or ``None`` if the PR is focused.

    Args:
        file_categories: Categoriser output (#9). May be a generator;
            this function consumes it once.

    Returns:
        ``None`` when fewer than :data:`_MIN_RISK_CATEGORIES_FOR_WARNING`
        distinct risk categories from
        :data:`_RISK_CATEGORIES` appear across the PR. Otherwise a
        single ``severity="medium"`` warning whose evidence enumerates
        the touched risk categories alphabetically and the count.
    """

    rows = list(file_categories)
    touched = _risk_categories_touched(rows)
    if len(touched) < _MIN_RISK_CATEGORIES_FOR_WARNING:
        return None

    sorted_touched = sorted(touched)
    return EngineWarning(
        code=WARN_CODE_MIXED_CONCERN,
        severity=_SEVERITY,
        message=(
            "PR mixes "
            f"{len(touched)} unrelated risk concerns ("
            + ", ".join(sorted_touched)
            + "). Consider splitting so each concern can be reviewed "
            "independently (\u00a710.11)."
        ),
        evidence={
            "risk_categories_touched": sorted_touched,
            "count": len(touched),
            "threshold": _MIN_RISK_CATEGORIES_FOR_WARNING,
        },
    )


__all__ = [
    "WARN_CODE_MIXED_CONCERN",
    "mixed_concern_warning",
]
