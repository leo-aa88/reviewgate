"""Changed-file count tiers for hosted analysis (``docs/DESIGN.md`` §22.3; issue #41).

Pure classification used when shaping inputs for ``reviewgate-core`` and when
deciding fast-fail paths. The worker pipeline (issue #50) should call
:func:`classify_changed_file_count` before building engine inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

_FULL_INPUT_MAX_FILES: Final[int] = 300
_SUMMARY_INPUT_MAX_FILES: Final[int] = 1000

HUGE_PR_FAIL_FAST_MESSAGE: Final[str] = (
    "This PR changes more than 1000 files. ReviewGate considers it unreviewably "
    "large for normal human review. Split or narrow the PR before review."
)

PrFileTier = Literal["full", "summary_only", "fail_fast"]


@dataclass(frozen=True, slots=True)
class PrFileTierClassification:
    """Structured tier decision for a PR's ``changed_files`` count."""

    tier: PrFileTier
    skip_llm: bool
    fail_fast_message: str | None


def classify_changed_file_count(file_count: int) -> PrFileTierClassification:
    """Return the §22.3 tier for a non-negative GitHub ``changed_files`` total.

    Args:
        file_count: Number of changed files reported by GitHub for the PR.

    Returns:
        Tier, whether hosted LLM stages must be skipped, and the §22.3 user
        message when the PR is unreviewably large.

    Raises:
        ValueError: If ``file_count`` is negative.
    """

    if file_count < 0:
        msg = "changed_files count cannot be negative"
        raise ValueError(msg)

    if file_count > _SUMMARY_INPUT_MAX_FILES:
        return PrFileTierClassification(
            tier="fail_fast",
            skip_llm=True,
            fail_fast_message=HUGE_PR_FAIL_FAST_MESSAGE,
        )
    if file_count > _FULL_INPUT_MAX_FILES:
        return PrFileTierClassification(
            tier="summary_only",
            skip_llm=False,
            fail_fast_message=None,
        )
    return PrFileTierClassification(
        tier="full",
        skip_llm=False,
        fail_fast_message=None,
    )
