"""Contract tests for `reviewgate.core.schemas` (GitHub #2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reviewgate.core.schemas import (
    ChangedFile,
    EngineInput,
    FileCategoryRow,
    PRRecord,
    ReviewabilityReport,
    SplitHint,
    Warning,
)


def _minimal_pr() -> dict:
    return {
        "title": "Add feature",
        "body": "Details here.",
        "author": "alice",
        "base_branch": "main",
        "head_branch": "feature/x",
        "additions": 10,
        "deletions": 2,
        "changed_files": 1,
    }


def test_engine_input_minimal_valid() -> None:
    payload = {
        "pr": _minimal_pr(),
        "files": [
            {
                "filename": "src/a.py",
                "status": "modified",
                "additions": 10,
                "deletions": 2,
                "changes": 12,
            }
        ],
        "config": {},
    }
    model = EngineInput.model_validate(payload)
    assert model.pr.title == "Add feature"
    assert model.files[0].filename == "src/a.py"
    assert model.config == {}


def test_engine_input_rejects_unknown_top_level_keys() -> None:
    payload = {
        "pr": _minimal_pr(),
        "files": [],
        "config": {},
        "extra_field": 1,
    }
    with pytest.raises(ValidationError) as exc:
        EngineInput.model_validate(payload)
    assert "extra_field" in str(exc.value).lower() or "extra" in str(exc.value).lower()


def test_engine_input_rejects_wrong_file_status() -> None:
    payload = {
        "pr": _minimal_pr(),
        "files": [
            {
                "filename": "src/a.py",
                "status": "typo",
                "additions": 0,
                "deletions": 0,
                "changes": 0,
            }
        ],
    }
    with pytest.raises(ValidationError):
        EngineInput.model_validate(payload)


def test_engine_input_strict_no_string_to_int_coercion() -> None:
    pr = _minimal_pr()
    pr["additions"] = "10"  # type: ignore[assignment]
    payload = {"pr": pr, "files": []}
    with pytest.raises(ValidationError):
        EngineInput.model_validate(payload)


def test_reviewability_report_round_trip() -> None:
    report = ReviewabilityReport(
        reviewability="WARN",
        stats={"human_loc_changed": 900},
        warnings=[
            Warning(
                code="large_human_diff",
                severity="medium",
                message="Too big",
                evidence={"human_loc_changed": 900, "threshold": 800},
            )
        ],
        suggested_labels=["reviewability-warn"],
        file_categories=[
            FileCategoryRow(
                filename="app/auth/session.ts",
                categories=["source", "auth"],
                risky=True,
                human_authored=True,
                changes=120,
            )
        ],
        split_hints=[SplitHint(title="Auth only", scope="session changes")],
        reviewer_checklist=["Confirm auth behavior is intentional."],
    )
    data = report.model_dump(mode="json")
    parsed = ReviewabilityReport.model_validate(data)
    assert parsed == report


def test_warning_requires_severity_and_code() -> None:
    with pytest.raises(ValidationError):
        Warning.model_validate(
            {
                "code": "x",
                "severity": "mega",
                "message": "bad severity enum",
            }
        )
