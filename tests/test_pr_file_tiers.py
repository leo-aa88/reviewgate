"""Tests for :mod:`reviewgate.app.analysis.pr_file_tiers` (issue #41)."""

from __future__ import annotations

import pytest

from reviewgate.app.analysis.pr_file_tiers import (
    HUGE_PR_FAIL_FAST_MESSAGE,
    classify_changed_file_count,
)


@pytest.mark.parametrize(
    ("count", "tier", "skip_llm", "message"),
    [
        (0, "full", False, None),
        (299, "full", False, None),
        (300, "full", False, None),
        (301, "summary_only", False, None),
        (302, "summary_only", False, None),
        (999, "summary_only", False, None),
        (1000, "summary_only", False, None),
        (1001, "fail_fast", True, HUGE_PR_FAIL_FAST_MESSAGE),
        (1002, "fail_fast", True, HUGE_PR_FAIL_FAST_MESSAGE),
    ],
)
def test_classify_changed_file_count_tiers(
    count: int,
    tier: str,
    skip_llm: bool,
    message: str | None,
) -> None:
    result = classify_changed_file_count(count)
    assert result.tier == tier
    assert result.skip_llm is skip_llm
    assert result.fail_fast_message == message


def test_classify_changed_file_count_rejects_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        classify_changed_file_count(-1)
