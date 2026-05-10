"""Deterministic report assembly: warnings -> suggested labels (\u00a713.9 + \u00a712).

Maps the warning codes emitted by the heuristic modules to the
user-configurable label names exposed by
:class:`reviewgate.core.config.Labels`.

The mapping is closed and one-way:

* Verdict label -- exactly one of ``labels.pass`` / ``labels.warn`` /
  ``labels.fail`` is always added based on
  :class:`reviewgate.core.schemas.Reviewability`.
* Concern labels -- added when at least one warning of a given concern
  is present:

  +-----------------------------+--------------------------------------+
  | Triggering warning code(s)  | Label config field (\u00a713.9)          |
  +=============================+======================================+
  | ``too_many_files_changed``  | ``too_large``                        |
  | ``too_large_human_loc``     |                                      |
  +-----------------------------+--------------------------------------+
  | ``weak_pr_body``            | ``missing_context``                  |
  | ``missing_linked_issue``    |                                      |
  +-----------------------------+--------------------------------------+
  | ``risky_paths_without_      | ``risky_change``                     |
  | rationale``                 |                                      |
  +-----------------------------+--------------------------------------+
  | ``mixed_concern_clusters``  | ``needs_split``                      |
  +-----------------------------+--------------------------------------+

Output ordering is stable: the verdict label first, then the concern
labels in the spec's enumeration order. Duplicates are suppressed so
e.g. two size warnings yield a single ``too_large`` label.

Pure: no I/O, no GitHub or LLM dependency.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from .config import Labels
from .linked_issue import WARN_CODE_MISSING_LINKED_ISSUE
from .mixed_concern import WARN_CODE_MIXED_CONCERN
from .pr_body import WARN_CODE_WEAK_BODY
from .risky_paths import WARN_CODE_RISKY_NO_RATIONALE
from .schemas import EngineWarning, Reviewability
from .size import WARN_CODE_TOO_LARGE_HUMAN_LOC, WARN_CODE_TOO_MANY_FILES

# Concern-label rules in spec enumeration order. Each entry pairs the
# set of warning codes that contribute to a concern with the
# :class:`Labels` attribute that supplies the configured label name.
# Adding a new heuristic means appending one row here; the rest of the
# function is data-driven.
_CONCERN_RULES: Final[tuple[tuple[frozenset[str], str], ...]] = (
    (
        frozenset({WARN_CODE_TOO_MANY_FILES, WARN_CODE_TOO_LARGE_HUMAN_LOC}),
        "too_large",
    ),
    (
        frozenset({WARN_CODE_WEAK_BODY, WARN_CODE_MISSING_LINKED_ISSUE}),
        "missing_context",
    ),
    (
        frozenset({WARN_CODE_RISKY_NO_RATIONALE}),
        "risky_change",
    ),
    (
        frozenset({WARN_CODE_MIXED_CONCERN}),
        "needs_split",
    ),
)


def _verdict_label(verdict: Reviewability, labels: Labels) -> str:
    """Return the configured label name for the overall verdict."""

    if verdict == "PASS":
        return labels.pass_
    if verdict == "WARN":
        return labels.warn
    return labels.fail


def suggested_labels(
    verdict: Reviewability,
    warnings: Iterable[EngineWarning],
    labels: Labels,
) -> list[str]:
    """Build the deterministic ``suggested_labels`` list (\u00a710.2 + \u00a713.9).

    Args:
        verdict: The aggregated reviewability from
            :func:`reviewgate.core.aggregate.baseline_reviewability`.
        warnings: The full warning list from
            :func:`reviewgate.core.engine.analyze`.
        labels: The effective :class:`reviewgate.core.config.Labels`
            block; user overrides from ``.reviewgate.yml`` propagate
            here unchanged.

    Returns:
        A deduplicated list of label names. The verdict label is
        always first; concern labels follow in the order defined by
        :data:`_CONCERN_RULES` so the list is stable across runs.
    """

    codes = {w.code for w in warnings}
    out: list[str] = [_verdict_label(verdict, labels)]
    seen: set[str] = {out[0]}
    for codes_for_concern, label_attr in _CONCERN_RULES:
        if codes & codes_for_concern:
            label_name = getattr(labels, label_attr)
            if label_name not in seen:
                seen.add(label_name)
                out.append(label_name)
    return out


__all__ = ["suggested_labels"]
