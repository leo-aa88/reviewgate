"""End-to-end tests for :func:`reviewgate.core.engine.analyze` (\u00a710 + #10).

These tests exercise the full pipeline that #10 wires up:

    EngineInput  ->  Categorizer (#9)  ->  compute_size_stats (\u00a710.4)  ->
    size_warnings (\u00a710.3)  ->  baseline_reviewability (\u00a710.13)
    ->  ReviewabilityReport (\u00a710.2).

Per-component behaviour is locked elsewhere (test_categorizer, test_paths,
test_size, test_aggregate); this module asserts that the orchestrator
hands data between them correctly and produces a \u00a710.2-shaped report.
"""

from __future__ import annotations

from typing import Final

import pytest

from reviewgate.core.engine import analyze
from reviewgate.core.schemas import (
    ChangedFile,
    EngineInput,
    PRRecord,
    Reviewability,
)
from reviewgate.core.size import (
    WARN_CODE_TOO_LARGE_HUMAN_LOC,
    WARN_CODE_TOO_MANY_FILES,
)

_AUTHOR: Final[str] = "octocat"

# A body well above the \u00a710.10 80-meaningful-char threshold AND
# carrying a `Closes #1` issue reference so neither the weak-body
# heuristic (#11) nor the missing-linked-issue heuristic (#12) fires
# by default. Tests that want to exercise either heuristic pass an
# explicit ``body=`` override.
_SUBSTANTIVE_BODY: Final[str] = (
    "Closes #1.\n\n"
    "This pull request implements a focused improvement to the API: "
    "it adds caching for the user activity endpoint and updates the "
    "matching unit tests so the reviewer can confirm the new behaviour "
    "without spinning up a full environment."
)
_BODY_WITHOUT_ISSUE_REF: Final[str] = (
    "This pull request implements a focused improvement to the API "
    "by adjusting the validation logic in the user input layer so the "
    "reviewer can confirm the new behaviour without any extra setup."
)


def _pr(
    *,
    additions: int,
    deletions: int,
    changed_files: int,
    body: str = _SUBSTANTIVE_BODY,
) -> PRRecord:
    return PRRecord(
        title="t",
        body=body,
        author=_AUTHOR,
        base_branch="main",
        head_branch="feat",
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
    )


def _file(filename: str, *, changes: int) -> ChangedFile:
    return ChangedFile(
        filename=filename,
        status="modified",
        additions=changes,
        deletions=0,
        changes=changes,
    )


def test_analyze_small_pr_reports_pass_with_zero_warnings() -> None:
    """Tiny clean PR: no warnings, baseline PASS, real file_categories row."""

    engine_input = EngineInput(
        pr=_pr(additions=10, deletions=2, changed_files=1),
        files=[_file("README.md", changes=12)],
    )
    report = analyze(engine_input)

    pass_verdict: Reviewability = "PASS"
    assert report.reviewability == pass_verdict
    assert report.warnings == []
    assert len(report.file_categories) == 1
    assert "docs" in report.file_categories[0].categories
    assert report.stats["files_changed"] == 1
    assert report.stats["raw_loc_changed"] == 12
    assert report.stats["human_loc_changed"] == 12


def test_analyze_huge_human_loc_pr_emits_high_severity_warning() -> None:
    """A PR with 5000 human-authored LOC fires the FAIL-tier human-LOC warning.

    Baseline reviewability is ``WARN`` (\u00a710.13 returns WARN for a single
    ``high`` warning; the policy-based ``fail_on_huge_pr`` escalation
    that would lift this to FAIL is layered on by #15).
    """

    engine_input = EngineInput(
        pr=_pr(additions=5000, deletions=0, changed_files=1),
        files=[_file("src/feature.py", changes=5000)],
    )
    report = analyze(engine_input)

    warn_verdict: Reviewability = "WARN"
    assert report.reviewability == warn_verdict
    codes = [w.code for w in report.warnings]
    assert codes == [WARN_CODE_TOO_LARGE_HUMAN_LOC]
    assert report.warnings[0].severity == "high"
    assert report.warnings[0].evidence["tier"] == "fail"


def test_analyze_lockfile_dominated_pr_does_not_fail_on_size() -> None:
    """\u00a710.4 example narrative: 4200 raw / 350 human must not auto-FAIL.

    Lockfile churn is excluded from human LOC, so size-severity gates on
    the human number; baseline stays PASS even though the raw diff is
    huge.
    """

    engine_input = EngineInput(
        pr=_pr(additions=2100, deletions=2100, changed_files=2),
        files=[
            _file("package-lock.json", changes=3850),
            _file("src/utils.py", changes=350),
        ],
    )
    report = analyze(engine_input)

    pass_verdict: Reviewability = "PASS"
    assert report.reviewability == pass_verdict
    assert report.warnings == []
    assert report.stats["raw_loc_changed"] == 4200
    assert report.stats["excluded_loc_changed"] == 3850
    assert report.stats["human_loc_changed"] == 350


def test_analyze_many_files_pr_emits_medium_severity_warning() -> None:
    """30 changed source files: WARN-tier ``too_many_files_changed`` warning.

    Baseline reviewability stays ``PASS`` (\u00a710.13 needs at least 2
    medium warnings or 1 high to escalate to WARN). The warning itself
    is what matters for downstream label and report assembly.
    """

    files = [_file(f"src/mod{i}.py", changes=5) for i in range(30)]
    engine_input = EngineInput(
        pr=_pr(additions=150, deletions=0, changed_files=30),
        files=files,
    )
    report = analyze(engine_input)

    pass_verdict: Reviewability = "PASS"
    assert report.reviewability == pass_verdict
    [warning] = report.warnings
    assert warning.code == WARN_CODE_TOO_MANY_FILES
    assert warning.severity == "medium"
    assert warning.evidence["actual"] == 30
    assert warning.evidence["threshold"] == 25


def test_analyze_two_failing_dimensions_yield_fail_verdict() -> None:
    """Two FAIL warnings -> aggregator returns FAIL on the high count."""

    files = [_file(f"src/mod{i}.py", changes=100) for i in range(80)]
    engine_input = EngineInput(
        pr=_pr(additions=8000, deletions=0, changed_files=80),
        files=files,
    )
    report = analyze(engine_input)

    fail_verdict: Reviewability = "FAIL"
    assert report.reviewability == fail_verdict
    codes = sorted(w.code for w in report.warnings)
    assert codes == sorted([WARN_CODE_TOO_MANY_FILES, WARN_CODE_TOO_LARGE_HUMAN_LOC])
    assert all(w.severity == "high" for w in report.warnings)


def test_analyze_uses_user_provided_thresholds() -> None:
    """User config overrides the \u00a710.3 defaults end-to-end.

    Pass a stricter ``fail.files_changed`` and verify a 5-file PR
    crosses the user-set FAIL boundary (which would be silent under the
    \u00a710.3 default of 75).
    """

    files = [_file(f"src/mod{i}.py", changes=2) for i in range(5)]
    engine_input = EngineInput(
        pr=_pr(additions=10, deletions=0, changed_files=5),
        files=files,
        config={
            "thresholds": {
                "warn": {"files_changed": 3},
                "fail": {"files_changed": 5},
            },
        },
    )
    report = analyze(engine_input)

    [warning] = report.warnings
    assert warning.code == WARN_CODE_TOO_MANY_FILES
    assert warning.severity == "high"
    assert warning.evidence["threshold"] == 5
    assert warning.evidence["tier"] == "fail"


def test_analyze_silently_falls_back_to_defaults_on_invalid_config() -> None:
    """\u00a712 contract: malformed ``config`` must not crash analysis.

    Invalid config (unknown key, wrong type) is dropped; the engine
    continues with defaults so the deterministic report still ships.
    The companion config_invalid warning is emitted by ``load_config``
    in the surrounding pipeline, not by ``analyze`` itself.
    """

    engine_input = EngineInput(
        pr=_pr(additions=10, deletions=2, changed_files=1),
        files=[_file("README.md", changes=12)],
        config={"thresholds": "not-a-mapping"},
    )
    report = analyze(engine_input)

    pass_verdict: Reviewability = "PASS"
    assert report.reviewability == pass_verdict
    assert report.warnings == []


def test_analyze_emits_weak_body_warning_when_pr_body_is_empty() -> None:
    """\u00a710.10: an empty PR body fires a ``weak_pr_body`` medium warning.

    Verifies #11 is wired through ``analyze()`` end to end. An empty
    body also lacks any issue reference, so the linked-issue heuristic
    (#12) co-fires; this test isolates the weak-body assertion by
    filtering on code.
    """

    from reviewgate.core.pr_body import REASON_EMPTY, WARN_CODE_WEAK_BODY

    engine_input = EngineInput(
        pr=_pr(additions=10, deletions=2, changed_files=1, body=""),
        files=[_file("README.md", changes=12)],
    )
    report = analyze(engine_input)

    [warning] = [w for w in report.warnings if w.code == WARN_CODE_WEAK_BODY]
    assert warning.severity == "medium"
    assert warning.evidence["reason"] == REASON_EMPTY


def test_analyze_emits_missing_linked_issue_warning_by_default() -> None:
    """\u00a710.10 + \u00a712: default policy requires a linked issue; none -> warn.

    A substantive body without any \u00a710.10 reference still trips the
    heuristic on a default-config run.
    """

    from reviewgate.core.linked_issue import WARN_CODE_MISSING_LINKED_ISSUE

    engine_input = EngineInput(
        pr=_pr(
            additions=10,
            deletions=2,
            changed_files=1,
            body=_BODY_WITHOUT_ISSUE_REF,
        ),
        files=[_file("README.md", changes=12)],
    )
    report = analyze(engine_input)

    [warning] = report.warnings
    assert warning.code == WARN_CODE_MISSING_LINKED_ISSUE
    assert warning.severity == "medium"


def test_analyze_emits_risky_paths_without_rationale_warning_by_default() -> None:
    """\u00a710.10 + \u00a712: risky file + silent body -> high warning."""

    from reviewgate.core.risky_paths import WARN_CODE_RISKY_NO_RATIONALE

    engine_input = EngineInput(
        pr=_pr(
            additions=10,
            deletions=2,
            changed_files=1,
            body="Closes #1.\n\nQuick refactor of unrelated helpers in the codebase.",
        ),
        files=[_file("services/auth/login.py", changes=12)],
    )
    report = analyze(engine_input)

    [warning] = [w for w in report.warnings if w.code == WARN_CODE_RISKY_NO_RATIONALE]
    assert warning.severity == "high"
    assert warning.evidence["risky_files"] == ["services/auth/login.py"]
    assert warning.evidence["risky_categories"] == ["auth"]


def test_analyze_silences_risky_paths_warning_when_body_mentions_category() -> None:
    """A body that names the touched risky category passes the check."""

    engine_input = EngineInput(
        pr=_pr(
            additions=10,
            deletions=2,
            changed_files=1,
            body=(
                "Closes #1.\n\nAdjusts the authentication middleware so "
                "the session timeout is configurable per tenant."
            ),
        ),
        files=[_file("services/auth/login.py", changes=12)],
    )
    report = analyze(engine_input)
    assert report.warnings == []


def test_analyze_downgrades_risky_warning_when_policy_disabled() -> None:
    """\u00a712 `fail_on_risky_paths_without_context: false` -> medium severity."""

    from reviewgate.core.risky_paths import WARN_CODE_RISKY_NO_RATIONALE

    engine_input = EngineInput(
        pr=_pr(
            additions=10,
            deletions=2,
            changed_files=1,
            body="Closes #1.\n\nRefactor of unrelated UI rendering helpers in the project.",
        ),
        files=[_file("services/auth/login.py", changes=12)],
        config={"policy": {"fail_on_risky_paths_without_context": False}},
    )
    report = analyze(engine_input)

    [warning] = [w for w in report.warnings if w.code == WARN_CODE_RISKY_NO_RATIONALE]
    assert warning.severity == "medium"


def test_analyze_respects_require_linked_issue_disabled_via_config() -> None:
    """\u00a712 ``policy.require_linked_issue: false`` silences the heuristic."""

    engine_input = EngineInput(
        pr=_pr(
            additions=10,
            deletions=2,
            changed_files=1,
            body=_BODY_WITHOUT_ISSUE_REF,
        ),
        files=[_file("README.md", changes=12)],
        config={"policy": {"require_linked_issue": False}},
    )
    report = analyze(engine_input)
    assert report.warnings == []


def test_analyze_combines_size_weak_body_and_linked_issue_warnings() -> None:
    """A 5000-LOC PR with an empty body fires every heuristic.

    Empty body trips both #11 (weak body) and #12 (no linked issue
    found in title or body); 5000 LOC trips #10 (too-large human LOC).
    Result: 1 high + 2 medium warnings, baseline FAIL per \u00a710.13.
    """

    from reviewgate.core.linked_issue import WARN_CODE_MISSING_LINKED_ISSUE
    from reviewgate.core.pr_body import WARN_CODE_WEAK_BODY

    engine_input = EngineInput(
        pr=_pr(additions=5000, deletions=0, changed_files=1, body="   "),
        files=[_file("src/feature.py", changes=5000)],
    )
    report = analyze(engine_input)

    codes = sorted(w.code for w in report.warnings)
    assert codes == sorted(
        [
            WARN_CODE_WEAK_BODY,
            WARN_CODE_TOO_LARGE_HUMAN_LOC,
            WARN_CODE_MISSING_LINKED_ISSUE,
        ],
    )
    fail_verdict: Reviewability = "FAIL"
    # 1 high (size) + 2 medium (body + linked issue) -> FAIL per \u00a710.13.
    assert report.reviewability == fail_verdict


def test_analyze_file_categories_are_in_input_order() -> None:
    """Per-file categorisation rows preserve the input file order."""

    names = ["src/a.py", "tests/b.py", "docs/c.md"]
    files = [_file(n, changes=5) for n in names]
    engine_input = EngineInput(
        pr=_pr(additions=15, deletions=0, changed_files=3),
        files=files,
    )
    report = analyze(engine_input)

    assert [r.filename for r in report.file_categories] == names
