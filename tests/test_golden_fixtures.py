"""Golden fixture tests for the §24.2 PR catalog (issue #17).

The fixtures live in :mod:`tests.fixtures.m2_golden`; this runner is
the CI gate that proves the deterministic engine still classifies
each canonical PR shape the way §24.2 says it should. Each case is
parametrized by its :class:`~tests.fixtures.m2_golden.manifest.GoldenCase`
slug so a regression points at the exact §24.2 row that broke.

Per-case assertions:

* The JSON file parses against :class:`reviewgate.core.schemas.EngineInput`
  (catches schema drift between fixtures and the engine input contract).
* :func:`reviewgate.core.engine.analyze` returns the verdict the
  manifest pins (catches §10.13 aggregation regressions).
* Every code in ``expected_warning_codes`` appears in the report and
  every code in ``forbidden_warning_codes`` does not (catches drift in
  individual heuristics).
* Every label in ``expected_label_subset`` appears in the
  ``suggested_labels`` list (catches §13.9 mapping regressions).
* The ``stats.files_changed`` count is at least
  ``expected_min_files_changed`` (catches a categorizer that silently
  drops files).

The output is also re-validated against
:class:`reviewgate.core.schemas.ReviewabilityReport` so a fixture run
also covers the §10.2 output schema contract end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixtures.m2_golden.manifest import CASES, GoldenCase

from reviewgate.core.engine import analyze
from reviewgate.core.schemas import EngineInput, ReviewabilityReport

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "m2_golden"


def _load(case: GoldenCase) -> EngineInput:
    """Read ``case.fixture_filename`` and validate it as an :class:`EngineInput`.

    Centralized so a schema-drift regression in any §24.2 fixture
    surfaces with a uniform error path instead of in 14 different
    test bodies.
    """

    fixture_path = _FIXTURE_DIR / case.fixture_filename
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    return EngineInput.model_validate(raw)


def test_manifest_covers_exactly_fourteen_cases() -> None:
    """§24.2 enumerates 14 PR cases; the manifest must list all of them.

    Locks the manifest's row count so an accidentally-deleted case
    fails the suite instead of silently dropping coverage.
    """

    assert len(CASES) == 14, (
        f"§24.2 enumerates 14 cases; manifest has {len(CASES)}. "
        "Add or restore the missing GoldenCase entry."
    )


def test_every_case_slug_is_unique() -> None:
    """Slugs are pytest ids; duplicates would shadow each other in output."""

    slugs = [case.slug for case in CASES]
    assert len(slugs) == len(set(slugs)), (
        f"Duplicate GoldenCase slugs detected: {slugs}"
    )


def test_every_case_fixture_file_exists() -> None:
    """Each manifest row must point at a real JSON file in the directory.

    Catches a copy-paste rename where the manifest entry is updated
    but the on-disk fixture is not (or vice-versa).
    """

    for case in CASES:
        fixture_path = _FIXTURE_DIR / case.fixture_filename
        assert fixture_path.is_file(), (
            f"Missing fixture file for {case.slug}: {fixture_path}"
        )


def test_no_unreferenced_fixture_files_in_directory() -> None:
    """Every JSON in the fixture directory must be referenced by the manifest.

    Prevents drift where a fixture is added on disk but never wired
    into the runner -- which would be silent dead code.
    """

    on_disk = {p.name for p in _FIXTURE_DIR.glob("*.json")}
    referenced = {case.fixture_filename for case in CASES}
    orphans = on_disk - referenced
    assert not orphans, (
        f"Fixture files not referenced by manifest: {sorted(orphans)}. "
        "Either add a GoldenCase or delete the file."
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.slug)
def test_golden_case_matches_manifest_invariants(case: GoldenCase) -> None:
    """Run :func:`analyze` on the §24.2 fixture and assert manifest invariants.

    This is the §24.2 acceptance test for issue #17: a single
    parametrized test that drives every PR shape through the full
    deterministic engine and asserts the verdict, warning codes,
    forbidden codes, suggested labels, and files-changed sanity
    bound the manifest pins for that shape.
    """

    engine_input = _load(case)
    report = analyze(engine_input)

    serialized = report.model_dump(mode="json")
    revalidated = ReviewabilityReport.model_validate(serialized)
    assert revalidated.reviewability == report.reviewability, (
        f"{case.slug}: report failed §10.2 round-trip validation"
    )

    assert report.reviewability == case.expected_reviewability, (
        f"{case.slug} ({case.description}): expected verdict "
        f"{case.expected_reviewability!r}, got {report.reviewability!r}. "
        f"Warnings: {[w.code for w in report.warnings]}"
    )

    actual_codes = {w.code for w in report.warnings}
    missing = case.expected_warning_codes - actual_codes
    assert not missing, (
        f"{case.slug}: missing expected warning codes {sorted(missing)}; "
        f"got {sorted(actual_codes)}"
    )
    forbidden = case.forbidden_warning_codes & actual_codes
    assert not forbidden, (
        f"{case.slug}: forbidden warning codes fired {sorted(forbidden)}; "
        f"got {sorted(actual_codes)}"
    )

    actual_labels = set(report.suggested_labels)
    missing_labels = case.expected_label_subset - actual_labels
    assert not missing_labels, (
        f"{case.slug}: missing expected suggested_labels "
        f"{sorted(missing_labels)}; got {sorted(actual_labels)}"
    )

    files_changed = report.stats.get("files_changed")
    assert isinstance(files_changed, int), (
        f"{case.slug}: stats.files_changed must be an int, got "
        f"{type(files_changed).__name__}"
    )
    assert files_changed >= case.expected_min_files_changed, (
        f"{case.slug}: stats.files_changed={files_changed} below "
        f"expected_min_files_changed={case.expected_min_files_changed}; "
        "categorizer may have dropped rows"
    )
