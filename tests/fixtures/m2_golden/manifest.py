"""Golden fixture manifest for the §24.2 PR catalog (issue #17).

Each :class:`GoldenCase` pairs one of the 14 §24.2 PR descriptions with
its on-disk JSON fixture and the deterministic invariants that case is
supposed to lock in. The runner in
``tests/test_golden_fixtures.py`` parametrizes over :data:`CASES`,
loads ``fixture_filename`` from this directory, validates it against
:class:`reviewgate.core.schemas.EngineInput`, runs
:func:`reviewgate.core.engine.analyze`, and asserts the per-case
expectations.

The manifest is intentionally a strongly-typed Python module rather
than a side-channel JSON / YAML file:

* ``targeted_heuristics`` doubles as living documentation for which
  §10 heuristics each fixture is engineered to exercise.
* ``expected_warning_codes`` and ``forbidden_warning_codes`` are
  ``frozenset[str]`` so the runner can check membership without
  caring about ordering.
* ``expected_label_subset`` is also a ``frozenset[str]`` because the
  §13.9 ``suggested_labels`` order is implementation-defined for
  concern labels (the verdict label position is asserted separately
  in the small-clean / fail cases that need it).
* ``expected_reviewability`` is an explicit
  :class:`reviewgate.core.schemas.Reviewability` rather than a free
  string so a typo here turns into a ``mypy --strict`` error.

Adding a 15th case is intentionally cheap: drop a JSON file in this
directory, then append one :class:`GoldenCase` here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from reviewgate.core.schemas import Reviewability


@dataclass(frozen=True)
class GoldenCase:
    """One row in the §24.2 fixture catalog (issue #17).

    Attributes:
        slug: Stable test id; used as the pytest parameter id so
            failures print as e.g. ``07_auth_change_without_context``.
        description: One-line summary mirroring the §24.2 wording.
        fixture_filename: JSON file alongside this module, validated
            against :class:`reviewgate.core.schemas.EngineInput`.
        targeted_heuristics: Human-readable list of §10 heuristics
            this case is engineered to exercise. Documentary; the
            runner does not assert against it directly.
        expected_reviewability: Verdict the engine must return for
            this fixture. Pinning the verdict catches regressions in
            the §10.13 aggregator.
        expected_warning_codes: Stable §10.12 warning codes that
            must be present. Subset semantics: extra codes are not
            failures unless listed in :attr:`forbidden_warning_codes`.
        forbidden_warning_codes: §10.12 codes that must NOT be
            present. Use sparingly -- only for codes the case is
            specifically designed to *not* trip.
        expected_label_subset: Subset of §13.9 ``suggested_labels``
            that must appear. Order is implementation-defined for
            concern labels; the runner only asserts membership.
        expected_min_files_changed: Lower bound on
            ``stats.files_changed`` so a categorizer regression that
            silently drops files is caught.
    """

    slug: str
    description: str
    fixture_filename: str
    targeted_heuristics: tuple[str, ...]
    expected_reviewability: Reviewability
    expected_warning_codes: frozenset[str] = field(default_factory=frozenset)
    forbidden_warning_codes: frozenset[str] = field(default_factory=frozenset)
    expected_label_subset: frozenset[str] = field(default_factory=frozenset)
    expected_min_files_changed: int = 1


CASES: Final[tuple[GoldenCase, ...]] = (
    GoldenCase(
        slug="01_small_clean_pr",
        description="small clean PR",
        fixture_filename="01_small_clean_pr.json",
        targeted_heuristics=("baseline PASS", "no warnings"),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset(),
        forbidden_warning_codes=frozenset(
            {
                "too_many_files_changed",
                "too_large_human_loc",
                "weak_pr_body",
                "missing_linked_issue",
                "risky_paths_without_rationale",
                "mixed_concern_clusters",
            }
        ),
        expected_label_subset=frozenset({"reviewability-pass"}),
        expected_min_files_changed=2,
    ),
    GoldenCase(
        slug="02_large_human_authored_pr",
        description="large human-authored PR",
        fixture_filename="02_large_human_authored_pr.json",
        targeted_heuristics=("§10.3 human_loc warn", "§10.4 human-authored LOC"),
        expected_reviewability="WARN",
        expected_warning_codes=frozenset({"too_large_human_loc"}),
        forbidden_warning_codes=frozenset({"missing_linked_issue", "weak_pr_body"}),
        expected_label_subset=frozenset({"reviewability-warn", "too-large"}),
        expected_min_files_changed=4,
    ),
    GoldenCase(
        slug="03_large_lockfile_only_pr",
        description="large lockfile-only PR",
        fixture_filename="03_large_lockfile_only_pr.json",
        targeted_heuristics=("§10.4 lockfile exclusion", "no size warning"),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset(),
        forbidden_warning_codes=frozenset(
            {"too_large_human_loc", "too_many_files_changed"}
        ),
        expected_label_subset=frozenset({"reviewability-pass"}),
        expected_min_files_changed=2,
    ),
    GoldenCase(
        slug="04_generated_code_pr",
        description="generated code PR",
        fixture_filename="04_generated_code_pr.json",
        targeted_heuristics=("§10.8 generated exclusion", "no size warning"),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset(),
        forbidden_warning_codes=frozenset(
            {"too_large_human_loc", "too_many_files_changed"}
        ),
        expected_label_subset=frozenset({"reviewability-pass"}),
        expected_min_files_changed=2,
    ),
    GoldenCase(
        slug="05_snapshot_heavy_pr",
        description="snapshot-heavy PR",
        fixture_filename="05_snapshot_heavy_pr.json",
        targeted_heuristics=("§10.8 snapshot exclusion", "no size warning"),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset(),
        forbidden_warning_codes=frozenset(
            {"too_large_human_loc", "too_many_files_changed"}
        ),
        expected_label_subset=frozenset({"reviewability-pass"}),
        expected_min_files_changed=2,
    ),
    GoldenCase(
        slug="06_risky_migration_pr",
        description="risky migration PR",
        fixture_filename="06_risky_migration_pr.json",
        targeted_heuristics=("§10.6 risky path", "§10.10 rationale present"),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset({"missing_tests_for_source"}),
        forbidden_warning_codes=frozenset(
            {
                "risky_paths_without_rationale",
                "mixed_concern_clusters",
                "weak_pr_body",
                "missing_linked_issue",
            }
        ),
        expected_label_subset=frozenset({"reviewability-pass", "needs-tests"}),
        expected_min_files_changed=1,
    ),
    GoldenCase(
        slug="07_auth_change_without_context",
        description="auth change without context",
        fixture_filename="07_auth_change_without_context.json",
        targeted_heuristics=(
            "§10.10 risky-path rationale",
            "§10.10 weak body",
            "§10.10 missing linked issue",
        ),
        expected_reviewability="FAIL",
        expected_warning_codes=frozenset(
            {
                "risky_paths_without_rationale",
                "weak_pr_body",
                "missing_linked_issue",
                "missing_tests_for_source",
            }
        ),
        expected_label_subset=frozenset(
            {
                "reviewability-fail",
                "missing-context",
                "risky-change",
                "needs-tests",
            }
        ),
        expected_min_files_changed=2,
    ),
    GoldenCase(
        slug="08_dependency_update_only",
        description="dependency update only",
        fixture_filename="08_dependency_update_only.json",
        targeted_heuristics=(
            "§10.7 dependency + lockfile categorization",
            "§10.4 lockfile exclusion",
        ),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset({"many_dependency_files"}),
        forbidden_warning_codes=frozenset(
            {
                "too_large_human_loc",
                "risky_paths_without_rationale",
                "mixed_concern_clusters",
            }
        ),
        expected_label_subset=frozenset({"reviewability-pass", "dependency-change"}),
        expected_min_files_changed=2,
    ),
    GoldenCase(
        slug="09_dependency_update_plus_behavior_change",
        description="dependency update plus behavior change",
        fixture_filename="09_dependency_update_plus_behavior_change.json",
        targeted_heuristics=(
            "§10.7 dependency + source mix",
            "no §10.10 risky / weak-body warnings",
        ),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset({"many_dependency_files"}),
        forbidden_warning_codes=frozenset(
            {
                "weak_pr_body",
                "missing_linked_issue",
                "risky_paths_without_rationale",
                "mixed_concern_clusters",
            }
        ),
        expected_label_subset=frozenset({"reviewability-pass", "dependency-change"}),
        expected_min_files_changed=4,
    ),
    GoldenCase(
        slug="10_source_tests_docs_normal_feature",
        description="source + tests + docs normal feature",
        fixture_filename="10_source_tests_docs_normal_feature.json",
        targeted_heuristics=(
            "§10.5 source + test + docs categories",
            "baseline PASS for healthy feature PRs",
        ),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset(),
        forbidden_warning_codes=frozenset(
            {
                "weak_pr_body",
                "missing_linked_issue",
                "too_large_human_loc",
                "risky_paths_without_rationale",
                "mixed_concern_clusters",
            }
        ),
        expected_label_subset=frozenset({"reviewability-pass"}),
        expected_min_files_changed=4,
    ),
    GoldenCase(
        slug="11_billing_auth_infra_suspicious_mixed_concern",
        description="billing + auth + infra suspicious mixed concern",
        fixture_filename="11_billing_auth_infra_suspicious_mixed_concern.json",
        targeted_heuristics=(
            "§10.11 mixed concern (3+ risk categories)",
            "§10.10 risky paths without rationale",
            "§10.10 weak body / missing linked issue",
        ),
        expected_reviewability="FAIL",
        expected_warning_codes=frozenset(
            {
                "mixed_concern_clusters",
                "risky_paths_without_rationale",
                "weak_pr_body",
                "missing_linked_issue",
            }
        ),
        expected_label_subset=frozenset(
            {
                "reviewability-fail",
                "missing-context",
                "risky-change",
                "needs-split",
            }
        ),
        expected_min_files_changed=3,
    ),
    GoldenCase(
        slug="12_massive_refactor",
        description="massive refactor",
        fixture_filename="12_massive_refactor.json",
        targeted_heuristics=(
            "§10.3 fail tier on files_changed and human_loc_changed",
            "§10.13 FAIL aggregation on high-severity warnings",
        ),
        expected_reviewability="FAIL",
        expected_warning_codes=frozenset(
            {"too_many_files_changed", "too_large_human_loc"}
        ),
        forbidden_warning_codes=frozenset(
            {"weak_pr_body", "missing_linked_issue"}
        ),
        expected_label_subset=frozenset({"reviewability-fail", "too-large"}),
        expected_min_files_changed=70,
    ),
    GoldenCase(
        slug="13_docs_only_pr",
        description="docs-only PR",
        fixture_filename="13_docs_only_pr.json",
        targeted_heuristics=("§10.5 docs category", "baseline PASS"),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset(),
        forbidden_warning_codes=frozenset(
            {
                "too_large_human_loc",
                "weak_pr_body",
                "missing_linked_issue",
                "risky_paths_without_rationale",
                "mixed_concern_clusters",
            }
        ),
        expected_label_subset=frozenset({"reviewability-pass"}),
        expected_min_files_changed=3,
    ),
    GoldenCase(
        slug="14_test_only_pr",
        description="test-only PR",
        fixture_filename="14_test_only_pr.json",
        targeted_heuristics=("§10.9 test category", "baseline PASS"),
        expected_reviewability="PASS",
        expected_warning_codes=frozenset(),
        forbidden_warning_codes=frozenset(
            {
                "too_large_human_loc",
                "weak_pr_body",
                "missing_linked_issue",
                "risky_paths_without_rationale",
                "mixed_concern_clusters",
            }
        ),
        expected_label_subset=frozenset({"reviewability-pass"}),
        expected_min_files_changed=2,
    ),
)


__all__ = ["CASES", "GoldenCase"]
