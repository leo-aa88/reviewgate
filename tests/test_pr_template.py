"""Regression tests for deterministic template conformance (issue #170)."""

from __future__ import annotations

from reviewgate.core.config import ReviewGateConfig
from reviewgate.core.engine import analyze
from reviewgate.core.pr_template import (
    WARN_CODE_PR_TEMPLATE_NOT_FOLLOWED,
    pr_template_warning,
)
from reviewgate.core.schemas import EngineInput, PRRecord


TEMPLATE = """\
## What and why

<!-- Describe the change. -->

## Design link

<!-- Add a design reference. -->

## Type of change

- [ ] Bug fix
- [ ] Feature

## Checklist

- [ ] Run tests <!-- required -->
- [ ] Update optional docs

## Verdict / behavior diff (optional but encouraged)

<!-- Optional section. -->
"""

FILLED = """\
## What and why

This change improves the template parser and explains the implementation.

## Design link

docs/DESIGN.md section 10.

## Type of change

- [x] Feature
- [ ] Bug fix

## Checklist

- [x] Run tests
- [ ] Update optional docs
"""


def _check(
    body: str,
    template: str | None = TEMPLATE,
    *,
    enabled: bool = True,
    fail: bool = False,
    boxes: bool = False,
):
    return pr_template_warning(
        body,
        template,
        enabled=enabled,
        fail_on_violation=fail,
        check_required_boxes=boxes,
    )


def test_disabled_policy_and_absent_template_are_noops() -> None:
    assert _check("", enabled=False) is None
    assert _check("", template=None) is None
    assert _check("", template=" \n ") is None
    assert ReviewGateConfig().policy.require_pr_template is False


def test_filled_sections_ignore_optional_headings_and_optional_boxes() -> None:
    assert _check(FILLED) is None
    assert _check(FILLED, boxes=True) is None


def test_missing_section_is_actionable_and_single_warning() -> None:
    body = FILLED.replace(
        "## Design link\n\ndocs/DESIGN.md section 10.\n\n",
        "",
    )
    warning = _check(body)
    assert warning is not None
    assert warning.code == WARN_CODE_PR_TEMPLATE_NOT_FOLLOWED
    assert warning.severity == "medium"
    assert warning.evidence["missing_sections"] == ["Design link"]
    assert warning.evidence["empty_sections"] == []


def test_empty_section_and_unedited_template_are_detected() -> None:
    body = FILLED.replace(
        "docs/DESIGN.md section 10.",
        "<!-- Add a design reference. -->",
    )
    warning = _check(body)
    assert warning is not None
    assert warning.evidence["empty_sections"] == ["Design link"]

    original = _check(TEMPLATE)
    assert original is not None
    assert "What and why" in original.evidence["empty_sections"]
    assert "Design link" in original.evidence["empty_sections"]


def test_section_heading_inside_fenced_code_is_not_accepted() -> None:
    body = FILLED.replace(
        "## Design link\n\ndocs/DESIGN.md section 10.",
        "```\n## Design link\n```\n",
    )
    warning = _check(body)
    assert warning is not None
    assert warning.evidence["missing_sections"] == ["Design link"]


def test_case_and_whitespace_are_normalized_for_headings() -> None:
    body = FILLED.replace("## Design link", "##   DESIGN   LINK")
    assert _check(body) is None


def test_unannotated_boxes_are_never_required() -> None:
    body = FILLED.replace("- [x] Run tests", "- [ ] Run tests")
    assert _check(body, boxes=False) is None
    warning = _check(body, boxes=True)
    assert warning is not None
    assert warning.evidence["unchecked_required"] == ["Run tests"]
    assert "Update optional docs" not in warning.evidence["unchecked_required"]


def test_missing_required_checkbox_is_reported_when_enabled() -> None:
    body = FILLED.replace("- [x] Run tests\n", "")
    warning = _check(body, boxes=True)
    assert warning is not None
    assert warning.evidence["unchecked_required"] == ["Run tests"]


def test_fail_escalation_preserves_evidence() -> None:
    warning = _check("## What and why\n", fail=True)
    assert warning is not None
    assert warning.severity == "high"
    assert warning.evidence["missing_sections"]


def test_template_without_required_headings_is_noop() -> None:
    assert _check("body", "<!-- Hidden guidance only -->") is None


def _engine_input(body: str, *, fail: bool) -> EngineInput:
    return EngineInput(
        pr=PRRecord(
            title="Fixes #170",
            body=body,
            author="octocat",
            base_branch="main",
            head_branch="feature",
            additions=0,
            deletions=0,
            changed_files=0,
        ),
        files=[],
        pr_template=TEMPLATE,
        config={
            "policy": {
                "require_pr_template": True,
                "fail_on_pr_template": fail,
            }
        },
    )


def test_engine_warning_label_and_standard_warn_verdict() -> None:
    report = analyze(_engine_input("Fixes #170.", fail=False))
    assert report.reviewability == "WARN"
    assert "pr-template-not-followed" in report.suggested_labels
    assert any(
        item["code"] == WARN_CODE_PR_TEMPLATE_NOT_FOLLOWED
        for item in report.model_dump(mode="json")["warnings"]
    )


def test_engine_high_warning_combines_with_weak_body_to_fail() -> None:
    report = analyze(_engine_input("Fixes #170.", fail=True))
    assert report.reviewability == "FAIL"
    warnings = [item for item in report.warnings if item.code == WARN_CODE_PR_TEMPLATE_NOT_FOLLOWED]
    assert len(warnings) == 1
    assert warnings[0].severity == "high"


def test_existing_input_without_template_remains_compatible() -> None:
    original = _engine_input(FILLED, fail=True).model_dump(mode="json")
    original.pop("pr_template")
    assert EngineInput.model_validate(original).pr_template is None


def test_real_repository_template_accepts_checked_checklist() -> None:
    """Exercise the actual multi-line checklist shipped by ReviewGate."""

    from pathlib import Path

    template_path = Path(__file__).resolve().parents[1] / ".github" / "PULL_REQUEST_TEMPLATE.md"
    template = template_path.read_text(encoding="utf-8")

    checklist_item = next(
        line for line in template.splitlines() if line.startswith("- [ ] My change keeps")
    )
    checked_item = checklist_item.replace("- [ ]", "- [x]", 1)

    body = "\n".join(
        [
            "## What and why",
            "",
            "This PR adds opt-in template checks. Closes #170.",
            "",
            "## Design link",
            "",
            "docs/DESIGN.md section 10.10.",
            "",
            "## Type of change",
            "",
            "- [x] New feature / heuristic (non-breaking change that adds capability)",
            "",
            "## Checklist",
            "",
            checked_item,
            "",
            "## Notes for reviewers",
            "",
            "The template checker remains disabled by default.",
        ]
    )

    assert _check(body, template=template) is None


def test_unchecked_checkbox_options_are_not_implicitly_required() -> None:
    """Ordinary unchecked choices remain optional."""

    template = "\n".join(
        [
            "## Checklist",
            "",
            "- [ ] Run tests",
            "",
        ]
    )

    assert _check(template, template=template) is None


def test_four_backtick_fence_cannot_be_closed_by_three() -> None:
    """Nested 3-backtick text must not expose an H2 inside the outer block."""
    template = "## Testing\n\n<!-- Explain testing. -->\n"
    inside = "````\n```\n## Testing\nThis is code, not testing.\n```\n````\n"
    warning = _check(inside, template=template)
    assert warning is not None
    assert warning.evidence["missing_sections"] == ["Testing"]
    assert _check(inside + "## Testing\n\nRan regression tests.\n", template=template) is None


def test_fence_requires_matching_marker_length_and_clean_closer() -> None:
    template = "## Testing\n\n<!-- Explain testing. -->\n"
    for fake_closer in ("```", "~~~~", "```` trailing", "````x"):
        body = "````\n" + fake_closer + "\n## Testing\nFake.\n````\n"
        warning = _check(body, template=template)
        assert warning is not None
        assert warning.evidence["missing_sections"] == ["Testing"]
    valid = "````\n## Testing\nInside.\n`````   \n## Testing\nOutside.\n"
    assert _check(valid, template=template) is None


def test_tilde_fence_and_four_space_indentation() -> None:
    template = "## Testing\n\n<!-- Explain testing. -->\n"
    inside = "~~~~\n```\n## Testing\nIgnored.\n~~~~\n"
    warning = _check(inside, template=template)
    assert warning is not None
    assert warning.evidence["missing_sections"] == ["Testing"]
    # Four leading spaces are not a CommonMark fenced-code opener.
    assert _check("    ````\n## Testing\nActual prose.\n", template=template) is None
