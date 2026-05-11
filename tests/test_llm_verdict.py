"""Tests for §11.8 verdict merge (issue #61)."""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from reviewgate.core.schemas import Reviewability

from reviewgate.app.llm.verdict import max_severity, merge_final_reviewability


@pytest.mark.parametrize(
    ("det", "llm", "expected"),
    [
        ("PASS", "PASS", "PASS"),
        ("PASS", "WARN", "WARN"),
        ("PASS", "FAIL", "FAIL"),
        ("WARN", "PASS", "WARN"),
        ("WARN", "WARN", "WARN"),
        ("WARN", "FAIL", "FAIL"),
        ("FAIL", "PASS", "FAIL"),
        ("FAIL", "WARN", "FAIL"),
        ("FAIL", "FAIL", "FAIL"),
    ],
)
def test_merge_final_reviewability_never_downgrades(
    det: Reviewability,
    llm: Reviewability,
    expected: Reviewability,
) -> None:
    """LLM cannot reduce deterministic severity (§11.8)."""

    assert merge_final_reviewability(det, llm) == expected


def test_merge_final_reviewability_skips_when_llm_absent() -> None:
    assert merge_final_reviewability("WARN", None) == "WARN"


def test_max_severity_symmetric() -> None:
    pass_: Reviewability = "PASS"
    fail: Reviewability = "FAIL"
    assert max_severity(pass_, fail) == fail
    assert max_severity(fail, pass_) == fail
