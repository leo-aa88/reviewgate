"""Size statistics and size-based warnings (docs/DESIGN.md \u00a710.3, \u00a710.4).

Implements two responsibilities:

1. **Size statistics** -- the \u00a710.4 formula
   ``human_loc_changed = raw_loc_changed - excluded_loc_changed`` where
   excluded LOC is the sum of ``changes`` on rows the categorizer marked
   with ``human_authored=False`` (lockfiles, generated paths, snapshots,
   vendored trees, minified assets). The engine may further adjust
   ``human_loc_changed`` for known dependency automation authors (see
   :mod:`reviewgate.core.automation_pr`). Results are exposed verbatim in
   :attr:`ReviewabilityReport.stats`.

2. **Size warnings** -- map :class:`SizeStats` to \u00a710.12 warnings using
   the \u00a710.3 thresholds (``warn.files_changed`` / ``fail.files_changed``
   and ``warn.human_loc_changed`` / ``fail.human_loc_changed``).
   Severity is ``"high"`` when the fail threshold is hit and
   ``"medium"`` when only the warn threshold is hit; the same code
   string is reused so downstream consumers can dedupe by dimension.

Pure: no I/O, no GitHub or LLM dependency. Inputs are already-validated
:class:`reviewgate.core.schemas.FileCategoryRow` rows from the
categorizer and the four threshold integers from
:class:`reviewgate.core.config.ReviewGateConfig`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from pydantic import Field

from ._base import StrictModel
from .schemas import EngineWarning, FileCategoryRow, WarningSeverity

# Stable warning codes (\u00a710.12). One code per size dimension; severity
# distinguishes WARN vs FAIL so downstream label rules (\u00a713.9 ``too-large``)
# do not have to enumerate four separate codes.
WARN_CODE_TOO_MANY_FILES: Final[str] = "too_many_files_changed"
"""Emitted when ``files_changed`` reaches the warn or fail threshold."""

WARN_CODE_TOO_LARGE_HUMAN_LOC: Final[str] = "too_large_human_loc"
"""Emitted when ``human_loc_changed`` reaches the warn or fail threshold."""

_SEVERITY_FAIL: Final[WarningSeverity] = "high"
_SEVERITY_WARN: Final[WarningSeverity] = "medium"


class SizeStats(StrictModel):
    """Per-PR size totals (\u00a710.4 example shape).

    All counts are non-negative: ``human_loc_changed`` is clamped to
    zero in case a future categorizer change makes ``excluded`` exceed
    ``raw`` for a degenerate input.
    """

    raw_loc_changed: int = Field(
        ge=0,
        description="``additions + deletions`` from the PR record (\u00a710.4).",
    )
    excluded_loc_changed: int = Field(
        ge=0,
        description=(
            "Sum of ``changes`` for rows with ``human_authored=False`` from "
            "the categorizer (\u00a710.4: lockfile, generated, snapshot, "
            "vendored, minified)."
        ),
    )
    human_loc_changed: int = Field(
        ge=0,
        description=(
            "Remaining changed-line count after \u00a710.4 exclusions "
            "(``raw_loc_changed - excluded_loc_changed``). JSON field name "
            "is unchanged; used as the size-severity input by \u00a710.3 "
            "instead of raw LOC."
        ),
    )
    files_changed: int = Field(
        ge=0,
        description="Total changed-file count (\u00a710.3 ``files_changed``).",
    )
    additions: int = Field(
        ge=0,
        description="Raw additions from the PR record (\u00a710.1).",
    )
    deletions: int = Field(
        ge=0,
        description="Raw deletions from the PR record (\u00a710.1).",
    )


def compute_size_stats(
    *,
    additions: int,
    deletions: int,
    file_categories: Iterable[FileCategoryRow],
) -> SizeStats:
    """Build :class:`SizeStats` from a PR record and categorizer output.

    Manifest-only dependency PRs from known bots are handled in
    :func:`reviewgate.core.automation_pr.finalize_size_stats_for_pr_author`
    after this baseline is computed.

    Args:
        additions: ``EngineInput.pr.additions``.
        deletions: ``EngineInput.pr.deletions``.
        file_categories: Output of the categorizer (#9). Each row's
            ``changes`` is summed for excluded LOC when
            ``human_authored is False``.

    Returns:
        A populated :class:`SizeStats`. ``human_loc_changed`` is clamped
        to zero (defensive guard against degenerate inputs where the
        categorizer's per-file ``changes`` totals exceed
        ``additions + deletions``).

    Example:
        ``additions + deletions == 4200`` with ``package-lock.json`` (3850
        excluded) and ``src/utils.py`` (350 human) yields
        ``human_loc_changed == 350``.
    """

    rows = list(file_categories)
    excluded = sum(row.changes for row in rows if not row.human_authored)
    raw = additions + deletions
    human = raw - excluded
    if human < 0:
        human = 0
    return SizeStats(
        raw_loc_changed=raw,
        excluded_loc_changed=excluded,
        human_loc_changed=human,
        files_changed=len(rows),
        additions=additions,
        deletions=deletions,
    )


def size_warnings(
    stats: SizeStats,
    *,
    warn_files_changed: int,
    fail_files_changed: int,
    warn_human_loc_changed: int,
    fail_human_loc_changed: int,
) -> list[EngineWarning]:
    """Apply the \u00a710.3 ladder to :class:`SizeStats` and emit warnings.

    Args:
        stats: Output of :func:`compute_size_stats`.
        warn_files_changed: ``thresholds.warn.files_changed`` (\u00a710.3).
        fail_files_changed: ``thresholds.fail.files_changed`` (\u00a710.3).
        warn_human_loc_changed: ``thresholds.warn.human_loc_changed`` (\u00a710.3).
        fail_human_loc_changed: ``thresholds.fail.human_loc_changed`` (\u00a710.3).

    Returns:
        Zero, one, or two :class:`EngineWarning` items \u2014 at most one per
        dimension. Severity is ``"high"`` when the fail threshold is
        hit and ``"medium"`` when only the warn threshold is hit. Both
        thresholds are interpreted as inclusive lower bounds (matches
        the \u00a710.13 aggregator's "at or above" wording for thresholds).
    """

    warnings: list[EngineWarning] = []

    files_warning = _threshold_warning(
        code=WARN_CODE_TOO_MANY_FILES,
        actual=stats.files_changed,
        warn_threshold=warn_files_changed,
        fail_threshold=fail_files_changed,
        dimension="files_changed",
        unit="files",
    )
    if files_warning is not None:
        warnings.append(files_warning)

    human_loc_warning = _threshold_warning(
        code=WARN_CODE_TOO_LARGE_HUMAN_LOC,
        actual=stats.human_loc_changed,
        warn_threshold=warn_human_loc_changed,
        fail_threshold=fail_human_loc_changed,
        dimension="human_loc_changed",
        unit="lines",
    )
    if human_loc_warning is not None:
        warnings.append(human_loc_warning)

    return warnings


def _threshold_warning(
    *,
    code: str,
    actual: int,
    warn_threshold: int,
    fail_threshold: int,
    dimension: str,
    unit: str,
) -> EngineWarning | None:
    """Build a single \u00a710.12 warning for a size dimension, or ``None``.

    Returns:
        ``None`` when ``actual`` is below ``warn_threshold``. Otherwise a
        warning with ``severity="high"`` if ``actual >= fail_threshold``
        else ``severity="medium"``. ``evidence`` carries both the
        observed value and the threshold that fired so reviewers can
        reconstruct the decision without re-running the engine.
    """

    if actual >= fail_threshold:
        threshold = fail_threshold
        severity = _SEVERITY_FAIL
        tier = "fail"
    elif actual >= warn_threshold:
        threshold = warn_threshold
        severity = _SEVERITY_WARN
        tier = "warn"
    else:
        return None

    message = f"PR exceeds {tier} {dimension} threshold: {actual} {unit} (threshold {threshold})."
    return EngineWarning(
        code=code,
        severity=severity,
        message=message,
        evidence={
            "dimension": dimension,
            "actual": actual,
            "threshold": threshold,
            "tier": tier,
        },
    )


__all__ = [
    "SizeStats",
    "WARN_CODE_TOO_LARGE_HUMAN_LOC",
    "WARN_CODE_TOO_MANY_FILES",
    "compute_size_stats",
    "size_warnings",
]
