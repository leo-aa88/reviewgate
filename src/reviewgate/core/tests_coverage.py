"""Source changes without accompanying tests (``docs/DESIGN.md`` §9 examples).

Emits a single ``medium`` warning when at least one changed file is categorised
as ``source`` and none are categorised as ``test``. Docs-only, config-only, or
generated-only PRs do not trigger this heuristic.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from .schemas import EngineWarning, FileCategoryRow, WarningSeverity

WARN_CODE_MISSING_TESTS_FOR_SOURCE: Final[str] = "missing_tests_for_source"
_SEVERITY: Final[WarningSeverity] = "medium"
# Very large PRs often defer test updates; keep the heuristic for reviewable-sized diffs.
_MAX_FILES_FOR_MISSING_TESTS_HEURISTIC: Final[int] = 40


def missing_tests_for_source_warning(
    file_categories: Iterable[FileCategoryRow],
) -> EngineWarning | None:
    """Return a warning when source files change but no test files are present."""

    rows = list(file_categories)
    if len(rows) > _MAX_FILES_FOR_MISSING_TESTS_HEURISTIC:
        return None

    has_test = any("test" in row.categories for row in rows)
    if has_test:
        return None
    # Ignore generated / lockfile-only churn: those rows may still carry the
    # ``source`` extension label but are not human-authored review targets.
    sources_with_human = any(
        "source" in row.categories and row.human_authored for row in rows
    )
    if not sources_with_human:
        return None
    return EngineWarning(
        code=WARN_CODE_MISSING_TESTS_FOR_SOURCE,
        severity=_SEVERITY,
        message=(
            "This PR changes source files but no test files were detected in the "
            "same diff. Consider adding or updating tests if behaviour changed."
        ),
        evidence={"has_source": True, "has_test": False},
    )


__all__ = ["WARN_CODE_MISSING_TESTS_FOR_SOURCE", "missing_tests_for_source_warning"]
