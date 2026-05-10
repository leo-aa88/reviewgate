"""Contract tests for `reviewgate.core.schemas` (GitHub #2)."""

from __future__ import annotations

import builtins
import json
from typing import TypedDict

import pytest
from pydantic import ValidationError

from reviewgate.core.schemas import (
    ChangedFile,
    EngineInput,
    EngineWarning,
    FileCategoryRow,
    PRRecord,
    ReviewabilityReport,
    SplitHint,
)


class _MinimalPRFields(TypedDict):
    title: str
    body: str
    author: str
    base_branch: str
    head_branch: str
    additions: int
    deletions: int
    changed_files: int


def _minimal_pr() -> _MinimalPRFields:
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


def _assert_extra_forbidden(exc: ValidationError, *, loc: tuple[str | int, ...]) -> None:
    errors = exc.errors()
    assert any(e.get("type") == "extra_forbidden" and tuple(e.get("loc", ())) == loc for e in errors), errors


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
    _assert_extra_forbidden(exc.value, loc=("extra_field",))


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
    pr = dict(_minimal_pr())
    pr["additions"] = "10"
    payload = {"pr": pr, "files": []}
    with pytest.raises(ValidationError):
        EngineInput.model_validate(payload)


def test_engine_input_config_defaults_when_omitted() -> None:
    payload = {"pr": _minimal_pr(), "files": []}
    model = EngineInput.model_validate(payload)
    assert model.config == {}


def test_pr_record_rejects_unknown_keys() -> None:
    pr = dict(_minimal_pr())
    pr["titel"] = "typo"
    with pytest.raises(ValidationError) as exc:
        PRRecord.model_validate(pr)
    _assert_extra_forbidden(exc.value, loc=("titel",))


def test_changed_file_rejects_unknown_keys() -> None:
    row = {
        "filename": "a.py",
        "status": "added",
        "additions": 0,
        "deletions": 0,
        "changes": 0,
        "extra": 1,
    }
    with pytest.raises(ValidationError) as exc:
        ChangedFile.model_validate(row)
    _assert_extra_forbidden(exc.value, loc=("extra",))


def test_changed_file_strips_filename_whitespace() -> None:
    row = {
        "filename": "  spaced.py  ",
        "status": "modified",
        "additions": 1,
        "deletions": 0,
        "changes": 1,
    }
    f = ChangedFile.model_validate(row)
    assert f.filename == "spaced.py"


def test_pr_numeric_fields_ge_zero() -> None:
    pr = dict(_minimal_pr())
    pr["additions"] = -1
    with pytest.raises(ValidationError) as exc:
        PRRecord.model_validate(pr)
    assert any(e.get("type") == "greater_than_equal" for e in exc.value.errors())


def test_changed_file_patch_omitted_vs_explicit_none() -> None:
    base = {
        "filename": "x.py",
        "status": "modified",
        "additions": 0,
        "deletions": 0,
        "changes": 0,
    }
    a = ChangedFile.model_validate(dict(base))
    b = ChangedFile.model_validate({**base, "patch": None})
    assert a.patch is None
    assert b.patch is None


def test_engine_warning_rejects_unknown_top_level_keys() -> None:
    with pytest.raises(ValidationError) as exc:
        EngineWarning.model_validate(
            {
                "code": "x",
                "severity": "low",
                "message": "m",
                "evidence": {},
                "oops": 1,
            }
        )
    _assert_extra_forbidden(exc.value, loc=("oops",))


def test_engine_warning_rejects_bad_severity_literal() -> None:
    with pytest.raises(ValidationError):
        EngineWarning.model_validate(
            {
                "code": "x",
                "severity": "mega",
                "message": "bad severity enum",
            }
        )


def test_file_category_row_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        FileCategoryRow.model_validate(
            {
                "filename": "x",
                "categories": ["totally_made_up"],
                "risky": False,
                "human_authored": True,
                "changes": 0,
            }
        )


def test_file_category_row_rejects_empty_categories() -> None:
    with pytest.raises(ValidationError):
        FileCategoryRow.model_validate(
            {
                "filename": "x",
                "categories": [],
                "risky": False,
                "human_authored": True,
                "changes": 0,
            }
        )


def test_reviewability_report_round_trip() -> None:
    report = ReviewabilityReport(
        reviewability="WARN",
        stats={"human_loc_changed": 900},
        warnings=[
            EngineWarning(
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


def test_split_hint_scope_none_round_trip() -> None:
    hint = SplitHint(title="Only title", scope=None)
    dumped = hint.model_dump(mode="json")
    assert dumped.get("scope") is None
    assert SplitHint.model_validate(dumped) == hint


def test_design_doc_section_10_1_input_example_validates() -> None:
    """Verbatim field layout from docs/DESIGN.md §10.1 JSON (values concretized)."""
    raw = {
        "pr": {
            "title": "string",
            "body": "string",
            "author": "string",
            "base_branch": "string",
            "head_branch": "string",
            "additions": 0,
            "deletions": 0,
            "changed_files": 0,
        },
        "files": [
            {
                "filename": "string",
                "status": "added",
                "additions": 0,
                "deletions": 0,
                "changes": 0,
            }
        ],
        "config": {},
    }
    EngineInput.model_validate(raw)


def test_design_doc_section_10_2_output_empty_validates() -> None:
    raw = {
        "reviewability": "PASS",
        "stats": {},
        "warnings": [],
        "suggested_labels": [],
        "file_categories": [],
        "split_hints": [],
        "reviewer_checklist": [],
    }
    ReviewabilityReport.model_validate(raw)


def test_design_doc_section_10_5_file_category_example_validates() -> None:
    raw = {
        "filename": "app/auth/session.ts",
        "categories": ["source", "auth"],
        "risky": True,
        "human_authored": True,
        "changes": 120,
    }
    row = FileCategoryRow.model_validate(raw)
    assert row.filename == "app/auth/session.ts"


def test_design_doc_section_10_12_warning_example_validates() -> None:
    raw = {
        "code": "large_human_diff",
        "severity": "medium",
        "message": (
            "This PR changes 1,200 human-authored lines, above the warning threshold of 800."
        ),
        "evidence": {"human_loc_changed": 1200, "threshold": 800},
    }
    EngineWarning.model_validate(raw)


def test_engine_input_round_trip_json_bytes() -> None:
    payload = {"pr": _minimal_pr(), "files": [], "config": {"version": 1}}
    dumped = json.dumps(payload).encode("utf-8")
    restored = EngineInput.model_validate_json(dumped)
    assert restored.config == {"version": 1}


def test_reviewability_report_model_json_schema_has_core_properties() -> None:
    schema = ReviewabilityReport.model_json_schema()
    props = schema.get("properties", {})
    assert "reviewability" in props
    assert "warnings" in props
    assert "file_categories" in props


def test_engine_input_model_json_schema_marks_pr_required() -> None:
    schema = EngineInput.model_json_schema()
    required = set(schema.get("required", []))
    assert "pr" in required
    assert "files" in required


def test_engine_warning_does_not_shadow_builtin_warning() -> None:
    from reviewgate.core import schemas as schemas_mod
    from reviewgate.core.schemas import EngineWarning

    assert EngineWarning is not builtins.Warning
    assert getattr(schemas_mod, "Warning", None) is None


def test_evidence_round_trips_through_json_mode() -> None:
    w = EngineWarning(
        code="c",
        severity="high",
        message="m",
        evidence={"n": 1, "flag": True, "s": "x"},
    )
    json_str = w.model_dump_json()
    again = EngineWarning.model_validate_json(json_str)
    assert again.evidence == w.evidence
