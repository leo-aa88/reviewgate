"""Warn-tier counts for risky, dependency, and config files (``docs/DESIGN.md`` §10.3).

Emits at most one :class:`~reviewgate.core.schemas.EngineWarning` per dimension
when the number of matching changed files reaches ``thresholds.warn.*`` for that
dimension. Severities are always ``medium`` — there is no separate fail tier in
the spec for these counts; baseline aggregation (§10.13) still applies.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from .config import WarnThresholds
from .schemas import EngineWarning, FileCategoryRow, WarningSeverity

WARN_CODE_MANY_RISKY_FILES: Final[str] = "many_risky_files"
WARN_CODE_MANY_DEPENDENCY_FILES: Final[str] = "many_dependency_files"
WARN_CODE_MANY_CONFIG_FILES: Final[str] = "many_config_files"

_SEVERITY: Final[WarningSeverity] = "medium"


def warn_threshold_count_warnings(
    file_categories: Iterable[FileCategoryRow],
    thresholds: WarnThresholds,
) -> list[EngineWarning]:
    """Emit §10.3 warn-tier warnings for risky / dependency / config file counts."""

    rows = list(file_categories)
    risky_count = sum(1 for r in rows if r.risky)
    dep_count = sum(1 for r in rows if "dependency" in r.categories)
    cfg_count = sum(1 for r in rows if "config" in r.categories)

    out: list[EngineWarning] = []

    w = _count_warning(
        code=WARN_CODE_MANY_RISKY_FILES,
        actual=risky_count,
        threshold=thresholds.risky_files_changed,
        dimension="risky_files_changed",
        label="risky files",
    )
    if w is not None:
        out.append(w)

    w = _count_warning(
        code=WARN_CODE_MANY_DEPENDENCY_FILES,
        actual=dep_count,
        threshold=thresholds.dependency_files_changed,
        dimension="dependency_files_changed",
        label="dependency manifest files",
    )
    if w is not None:
        out.append(w)

    w = _count_warning(
        code=WARN_CODE_MANY_CONFIG_FILES,
        actual=cfg_count,
        threshold=thresholds.config_files_changed,
        dimension="config_files_changed",
        label="config files",
    )
    if w is not None:
        out.append(w)

    return out


def _count_warning(
    *,
    code: str,
    actual: int,
    threshold: int,
    dimension: str,
    label: str,
) -> EngineWarning | None:
    """Warn when ``actual`` is at or above ``threshold`` (inclusive)."""

    if threshold < 0 or actual < threshold:
        return None
    return EngineWarning(
        code=code,
        severity=_SEVERITY,
        message=(
            f"This PR touches {actual} {label}, at or above the warning threshold "
            f"of {threshold} ({dimension})."
        ),
        evidence={
            "dimension": dimension,
            "actual": actual,
            "threshold": threshold,
        },
    )


__all__ = [
    "WARN_CODE_MANY_CONFIG_FILES",
    "WARN_CODE_MANY_DEPENDENCY_FILES",
    "WARN_CODE_MANY_RISKY_FILES",
    "warn_threshold_count_warnings",
]
