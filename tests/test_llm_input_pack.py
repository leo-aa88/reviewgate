"""Tests for §11.5 LLM input packaging (issue #62)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pydantic")

from reviewgate.app.llm.input_pack import build_llm_user_message
from reviewgate.core.report import suggested_labels
from reviewgate.core.schemas import ChangedFile, PRRecord, ReviewabilityReport
from reviewgate.core.config import Labels


def test_build_llm_user_message_truncation_stays_valid_json() -> None:
    """Budget truncation must not leave the user message as invalid JSON."""

    huge_body = "word " * 50_000
    pr = PRRecord(
        title="title",
        body=huge_body,
        author="a",
        base_branch="main",
        head_branch="h",
        additions=1,
        deletions=0,
        changed_files=1,
    )
    report = ReviewabilityReport(
        reviewability="PASS",
        stats={"files_changed": 1},
        warnings=[],
        suggested_labels=suggested_labels("PASS", [], Labels()),
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )
    files = [
        ChangedFile(
            filename="x.py",
            status="modified",
            additions=1,
            deletions=0,
            changes=1,
        ),
    ]
    raw = build_llm_user_message(
        pr=pr,
        report=report,
        files=files,
        mode="full",
    )
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    assert parsed["pr_title"] == "title"
