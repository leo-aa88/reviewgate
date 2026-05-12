"""Tests for :mod:`reviewgate_action.summary` Markdown rendering."""

from __future__ import annotations

from reviewgate.core.schemas import ReviewabilityReport
from reviewgate_action.summary import render_summary


def test_render_summary_unknown_pr_author_kind_displays_raw_kind() -> None:
    """Fallback label uses the stats string when ``pr_author_kind`` is not in the map."""

    report = ReviewabilityReport(
        reviewability="PASS",
        stats={"pr_author_kind": "future_kind"},
    )
    rendered = render_summary(report)
    assert "future_kind" in rendered
    assert "PR author class" in rendered
