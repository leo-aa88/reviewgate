"""Tests for LLM report merge and hosted stage gating (issues #60–#62, #64)."""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from reviewgate.app.analysis.pipeline import PipelineAnalysisArtifacts
from reviewgate.app.llm.merge_report import apply_llm_to_deterministic_report
from reviewgate.app.llm.schemas import LlmReviewabilityReport
from reviewgate.app.llm.stage import maybe_apply_hosted_llm_stage
from reviewgate.app.settings import AppSettings
from reviewgate.core.config import Labels, ReviewGateConfig
from reviewgate.core.report import suggested_labels
from reviewgate.core.schemas import ChangedFile, PRRecord, ReviewabilityReport


def test_apply_llm_escalates_pass_to_warn() -> None:
    """Mocked LLM WARN with deterministic PASS yields final WARN."""

    det = ReviewabilityReport(
        reviewability="PASS",
        stats={"files_changed": 1, "raw_loc_changed": 2, "human_loc_changed": 2},
        warnings=[],
        suggested_labels=suggested_labels("PASS", [], Labels()),
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )
    llm = LlmReviewabilityReport(
        reviewability="WARN",
        summary="Scope unclear.",
        issues=[],
        suggested_labels=[],
        split_suggestions=[],
        reviewer_checklist=["Clarify intent"],
    )
    merged = apply_llm_to_deterministic_report(det, llm, labels=Labels())
    assert merged.reviewability == "WARN"
    assert "llm" in merged.stats
    assert merged.reviewer_checklist == ["Clarify intent"]


def test_llm_stage_skipped_when_llm_reports_false() -> None:
    """§21.3 default: no provider path when ``llm_reports`` is false."""

    det = ReviewabilityReport(
        reviewability="WARN",
        stats={},
        warnings=[],
        suggested_labels=[],
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )
    art = PipelineAnalysisArtifacts(
        pr=PRRecord(
            title="t",
            body="b",
            author="a",
            base_branch="main",
            head_branch="h",
            additions=1,
            deletions=0,
            changed_files=1,
        ),
        files=[
            ChangedFile(
                filename="f.py",
                status="modified",
                additions=1,
                deletions=0,
                changes=1,
            ),
        ],
        changed_files_count=1,
    )
    out = maybe_apply_hosted_llm_stage(
        AppSettings(),
        deterministic_report=det,
        effective_config=ReviewGateConfig(llm_reports=False),
        artifacts=art,
    )
    assert out.report is det
    assert out.llm_used is False


def test_llm_stage_skipped_without_artifacts() -> None:
    det = ReviewabilityReport(
        reviewability="FAIL",
        stats={},
        warnings=[],
        suggested_labels=[],
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )
    out = maybe_apply_hosted_llm_stage(
        AppSettings(),
        deterministic_report=det,
        effective_config=ReviewGateConfig(llm_reports=True),
        artifacts=None,
    )
    assert out.report is det
    assert out.llm_used is False
