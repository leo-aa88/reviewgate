"""Tests for :mod:`reviewgate.core.size` against \u00a710.3 / \u00a710.4.

Locks the \u00a710.4 formula and the \u00a710.3 threshold ladder:

* ``raw_loc_changed = additions + deletions``
* ``excluded_loc_changed = sum(changes for files marked not human-authored)``
* ``human_loc_changed = max(0, raw - excluded)``  *(\u00a710.4 example: 4200 raw, 350 human)*
* WARN warnings for ``files_changed`` and ``human_loc_changed`` use
  ``severity="medium"``; FAIL warnings use ``severity="high"``.
* Same warning code is reused across tiers (so \u00a713.9 ``too-large``
  label rules deduplicate by ``code``).

Tests build :class:`FileCategoryRow` rows directly so the size logic is
exercised independently of the categoriser.
"""

from __future__ import annotations

import pytest

from reviewgate.core.schemas import FileCategoryRow
from reviewgate.core.size import (
    WARN_CODE_TOO_LARGE_HUMAN_LOC,
    WARN_CODE_TOO_MANY_FILES,
    SizeStats,
    compute_size_stats,
    size_warnings,
)


def _row(
    filename: str,
    *,
    changes: int,
    human_authored: bool,
    risky: bool = False,
    category: str = "source",
) -> FileCategoryRow:
    """Build a minimal :class:`FileCategoryRow` for size accounting.

    Only ``changes`` and ``human_authored`` matter for \u00a710.4; the rest
    are pinned to spec-valid placeholders.
    """

    return FileCategoryRow(
        filename=filename,
        categories=[category],  # type: ignore[list-item]
        risky=risky,
        human_authored=human_authored,
        changes=changes,
    )


# --- §10.4 formula ----------------------------------------------------------


def test_compute_size_stats_matches_design_doc_example() -> None:
    """Lock the \u00a710.4 example: 4200 raw, ~3850 excluded, ~350 human.

    The exact split here uses one big lockfile (3850 excluded changes)
    so the human-LOC formula yields 350, matching the documented case.
    """

    files = [
        _row("package-lock.json", changes=3850, human_authored=False, category="lockfile"),
        _row("src/utils.py", changes=350, human_authored=True),
    ]
    stats = compute_size_stats(additions=2100, deletions=2100, file_categories=files)
    assert stats.raw_loc_changed == 4200
    assert stats.excluded_loc_changed == 3850
    assert stats.human_loc_changed == 350
    assert stats.files_changed == 2
    assert stats.additions == 2100
    assert stats.deletions == 2100


def test_compute_size_stats_excludes_only_non_human_files() -> None:
    """Files with ``human_authored=True`` never contribute to excluded LOC."""

    files = [
        _row("a.py", changes=10, human_authored=True),
        _row("b.py", changes=20, human_authored=True),
        _row("yarn.lock", changes=200, human_authored=False),
        _row("vendor/x.go", changes=50, human_authored=False),
    ]
    stats = compute_size_stats(additions=140, deletions=140, file_categories=files)
    assert stats.excluded_loc_changed == 250
    assert stats.human_loc_changed == 30  # 280 raw - 250 excluded


def test_compute_size_stats_clamps_negative_human_loc_to_zero() -> None:
    """Defensive: a degenerate input must not produce negative human LOC.

    GitHub guarantees ``changes == additions + deletions`` per file, so
    in practice excluded LOC <= raw LOC. The clamp protects against
    fixture drift and future per-file accounting changes.
    """

    files = [_row("yarn.lock", changes=999, human_authored=False, category="lockfile")]
    stats = compute_size_stats(additions=10, deletions=10, file_categories=files)
    assert stats.human_loc_changed == 0
    assert stats.excluded_loc_changed == 999


def test_compute_size_stats_with_empty_file_list() -> None:
    """Empty diffs are valid; size stats default to all zeros."""

    stats = compute_size_stats(additions=0, deletions=0, file_categories=[])
    assert stats == SizeStats(
        raw_loc_changed=0,
        excluded_loc_changed=0,
        human_loc_changed=0,
        files_changed=0,
        additions=0,
        deletions=0,
    )


def test_compute_size_stats_accepts_generator_input() -> None:
    """``Iterable[FileCategoryRow]`` accepts generators (one-shot consume)."""

    def gen() -> object:
        yield _row("a.py", changes=10, human_authored=True)
        yield _row("b.py", changes=10, human_authored=True)

    stats = compute_size_stats(
        additions=10,
        deletions=10,
        file_categories=(_row(f"f{i}.py", changes=5, human_authored=True) for i in range(4)),
    )
    assert stats.files_changed == 4
    assert stats.human_loc_changed == 20


def test_size_stats_serializes_to_plain_int_dict() -> None:
    """``SizeStats.model_dump()`` is folded into ``ReviewabilityReport.stats``.

    Verify the dict is JSON-friendly (every value is an ``int``) so the
    CLI can dump it through ``json.dumps`` without coercion.
    """

    stats = compute_size_stats(
        additions=5,
        deletions=5,
        file_categories=[_row("a.py", changes=10, human_authored=True)],
    )
    dumped = stats.model_dump()
    assert all(isinstance(v, int) for v in dumped.values())
    assert dumped["human_loc_changed"] == 10


# --- §10.3 thresholds: files_changed dimension ------------------------------


@pytest.mark.parametrize(
    ("files_changed", "expected_severity", "expected_tier"),
    [
        pytest.param(74, "medium", "warn", id="just-below-fail"),
        pytest.param(75, "high", "fail", id="fail-boundary"),
        pytest.param(120, "high", "fail", id="well-into-fail"),
        pytest.param(25, "medium", "warn", id="warn-boundary"),
        pytest.param(26, "medium", "warn", id="just-above-warn"),
    ],
)
def test_files_changed_warning_severity_tiers(
    files_changed: int,
    expected_severity: str,
    expected_tier: str,
) -> None:
    """\u00a710.3 inclusive thresholds: at-threshold counts trigger the tier."""

    files = [
        _row(f"f{i}.py", changes=1, human_authored=True) for i in range(files_changed)
    ]
    stats = compute_size_stats(additions=files_changed, deletions=0, file_categories=files)
    warnings = size_warnings(
        stats,
        warn_files_changed=25,
        fail_files_changed=75,
        warn_human_loc_changed=10_000,
        fail_human_loc_changed=20_000,
    )
    file_warnings = [w for w in warnings if w.code == WARN_CODE_TOO_MANY_FILES]
    assert len(file_warnings) == 1
    assert file_warnings[0].severity == expected_severity
    assert file_warnings[0].evidence["tier"] == expected_tier


def test_files_changed_below_warn_emits_no_warning() -> None:
    """Counts below the warn threshold produce no size warning at all."""

    files = [_row(f"f{i}.py", changes=1, human_authored=True) for i in range(24)]
    stats = compute_size_stats(additions=24, deletions=0, file_categories=files)
    warnings = size_warnings(
        stats,
        warn_files_changed=25,
        fail_files_changed=75,
        warn_human_loc_changed=10_000,
        fail_human_loc_changed=20_000,
    )
    assert [w.code for w in warnings if w.code == WARN_CODE_TOO_MANY_FILES] == []


# --- §10.3 thresholds: human_loc_changed dimension --------------------------


@pytest.mark.parametrize(
    ("human_loc", "expected_severity", "expected_tier"),
    [
        pytest.param(800, "medium", "warn", id="warn-boundary"),
        pytest.param(801, "medium", "warn", id="just-above-warn"),
        pytest.param(2499, "medium", "warn", id="just-below-fail"),
        pytest.param(2500, "high", "fail", id="fail-boundary"),
        pytest.param(50_000, "high", "fail", id="huge-pr"),
    ],
)
def test_human_loc_warning_severity_tiers(
    human_loc: int,
    expected_severity: str,
    expected_tier: str,
) -> None:
    """\u00a710.3 thresholds map onto severity exactly as documented."""

    files = [_row("a.py", changes=human_loc, human_authored=True)]
    stats = compute_size_stats(
        additions=human_loc, deletions=0, file_categories=files
    )
    warnings = size_warnings(
        stats,
        warn_files_changed=10_000,
        fail_files_changed=20_000,
        warn_human_loc_changed=800,
        fail_human_loc_changed=2_500,
    )
    loc_warnings = [w for w in warnings if w.code == WARN_CODE_TOO_LARGE_HUMAN_LOC]
    assert len(loc_warnings) == 1
    assert loc_warnings[0].severity == expected_severity
    assert loc_warnings[0].evidence["tier"] == expected_tier


def test_human_loc_below_warn_threshold_emits_no_warning() -> None:
    """``human_loc_changed`` strictly below warn threshold stays silent."""

    files = [_row("a.py", changes=799, human_authored=True)]
    stats = compute_size_stats(additions=799, deletions=0, file_categories=files)
    warnings = size_warnings(
        stats,
        warn_files_changed=10_000,
        fail_files_changed=20_000,
        warn_human_loc_changed=800,
        fail_human_loc_changed=2_500,
    )
    assert [w for w in warnings if w.code == WARN_CODE_TOO_LARGE_HUMAN_LOC] == []


def test_human_loc_uses_human_not_raw_per_design_note() -> None:
    """\u00a710.3 note: "use human_loc_changed, not raw LOC, for size severity".

    A 4200-raw-LOC PR dominated by a lockfile must not fail on size,
    matching the \u00a710.4 example narrative.
    """

    files = [
        _row("yarn.lock", changes=3850, human_authored=False, category="lockfile"),
        _row("src/utils.py", changes=350, human_authored=True),
    ]
    stats = compute_size_stats(additions=2100, deletions=2100, file_categories=files)
    warnings = size_warnings(
        stats,
        warn_files_changed=25,
        fail_files_changed=75,
        warn_human_loc_changed=800,
        fail_human_loc_changed=2_500,
    )
    assert warnings == [], (
        "human_loc=350 stays below WARN; raw_loc=4200 must not bypass "
        "the human-LOC severity gate"
    )


# --- combined-dimension behavior --------------------------------------------


def test_size_warnings_emits_at_most_one_per_dimension() -> None:
    """A single PR may trigger up to two warnings (files + human LOC)."""

    files = [_row(f"f{i}.py", changes=100, human_authored=True) for i in range(80)]
    stats = compute_size_stats(additions=8000, deletions=0, file_categories=files)
    warnings = size_warnings(
        stats,
        warn_files_changed=25,
        fail_files_changed=75,
        warn_human_loc_changed=800,
        fail_human_loc_changed=2_500,
    )
    codes = [w.code for w in warnings]
    assert codes.count(WARN_CODE_TOO_MANY_FILES) == 1
    assert codes.count(WARN_CODE_TOO_LARGE_HUMAN_LOC) == 1
    assert all(w.severity == "high" for w in warnings)


def test_size_warning_evidence_carries_threshold_and_actual() -> None:
    """\u00a710.12 evidence: reviewers can reconstruct the decision later."""

    files = [_row(f"f{i}.py", changes=1, human_authored=True) for i in range(80)]
    stats = compute_size_stats(additions=80, deletions=0, file_categories=files)
    warnings = size_warnings(
        stats,
        warn_files_changed=25,
        fail_files_changed=75,
        warn_human_loc_changed=10_000,
        fail_human_loc_changed=20_000,
    )
    [files_warning] = [w for w in warnings if w.code == WARN_CODE_TOO_MANY_FILES]
    assert files_warning.evidence == {
        "dimension": "files_changed",
        "actual": 80,
        "threshold": 75,
        "tier": "fail",
    }


def test_size_warnings_message_mentions_threshold_value() -> None:
    """Human-readable message includes both observed value and threshold."""

    files = [_row(f"f{i}.py", changes=1, human_authored=True) for i in range(30)]
    stats = compute_size_stats(additions=30, deletions=0, file_categories=files)
    [warning] = size_warnings(
        stats,
        warn_files_changed=25,
        fail_files_changed=75,
        warn_human_loc_changed=10_000,
        fail_human_loc_changed=20_000,
    )
    assert "30" in warning.message
    assert "25" in warning.message
    assert "warn" in warning.message
