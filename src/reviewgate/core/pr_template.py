"""Pure, deterministic PR-template conformance heuristic (issue #170)."""

from __future__ import annotations

import re
from typing import Final

from .pr_body import meaningful_text
from .schemas import EngineWarning, WarningSeverity

WARN_CODE_PR_TEMPLATE_NOT_FOLLOWED: Final[str] = "pr_template_not_followed"

_COMMENT: Final[re.Pattern[str]] = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING: Final[re.Pattern[str]] = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_FENCE: Final[re.Pattern[str]] = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CHECKBOX: Final[re.Pattern[str]] = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.+?)\s*$")
_REQUIRED: Final[re.Pattern[str]] = re.compile(r"<!--\s*required\s*-->", re.IGNORECASE)
_OPTIONAL: Final[re.Pattern[str]] = re.compile(r"\boptional\b", re.IGNORECASE)
_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")


def _normalize(value: str) -> str:
    """Return a stable comparison key for section and checkbox titles."""

    return _WHITESPACE.sub(" ", value).strip().casefold()


def _sections(markdown: str) -> list[tuple[str, str]]:
    """Extract H2 sections while ignoring comments and fenced-code headings."""

    cleaned = _COMMENT.sub("", markdown)
    result: list[tuple[str, str]] = []
    heading: str | None = None
    lines: list[str] = []
    fence: tuple[str, int] | None = None

    for line in cleaned.splitlines():
        delimiter = _FENCE.match(line)
        if delimiter is not None:
            run, trailing = delimiter.groups()
            marker = run[0]
            if fence is None:
                # A backtick opener cannot have backticks in its info string.
                if marker == "~" or "`" not in trailing:
                    fence = (marker, len(run))
            elif marker == fence[0] and len(run) >= fence[1] and not trailing.strip():
                # The closing run must be long enough and have no info string.
                fence = None
            if heading is not None:
                lines.append(line)
            continue

        match = _HEADING.match(line) if fence is None else None
        if match is not None:
            if heading is not None:
                result.append((heading, "\n".join(lines)))
            heading = match.group(1).strip()
            lines = []
        elif heading is not None:
            lines.append(line)

    if heading is not None:
        result.append((heading, "\n".join(lines)))
    return result


def _section_prose(content: str) -> str:
    """Remove checkbox scaffolding before applying PR-body normalization."""

    prose = "\n".join(line for line in content.splitlines() if _CHECKBOX.match(line) is None)
    return meaningful_text(prose)


def _section_filled(body: str, template: str) -> bool:
    """Check prose changes or checkbox choices without enforcing optional boxes."""

    actual = _section_prose(body)
    scaffold = _section_prose(template)

    if actual and actual != scaffold:
        return True

    # A choice-only section is present even if its optional boxes
    # remain unchecked. Explicitly required boxes are checked separately.
    if not actual and not scaffold:
        return any(_CHECKBOX.match(line) is not None for line in body.splitlines())

    # Wrapped checklist continuation lines may remain unchanged
    # even when a checkbox is selected.
    return bool(_checked_checkbox_labels(body) - _checked_checkbox_labels(template))


def _required_checkbox_labels(template: str) -> list[str]:
    """Recognize only boxes deliberately annotated <!-- required -->."""

    result: list[str] = []
    for line in template.splitlines():
        match = _CHECKBOX.match(line)
        if match is None or _REQUIRED.search(line) is None:
            continue
        title = _COMMENT.sub("", match.group(2)).strip()
        if title and _normalize(title) not in {_normalize(item) for item in result}:
            result.append(title)
    return result


def _checked_checkbox_labels(body: str) -> set[str]:
    """Return checked checkbox titles, ignoring hidden Markdown comments."""

    result: set[str] = set()
    for line in _COMMENT.sub("", body).splitlines():
        match = _CHECKBOX.match(line)
        if match is not None and match.group(1).lower() == "x":
            result.add(_normalize(match.group(2)))
    return result


def pr_template_warning(
    body: str,
    template: str | None,
    *,
    enabled: bool,
    fail_on_violation: bool = False,
    check_required_boxes: bool = False,
) -> EngineWarning | None:
    """Check required H2 sections and explicitly required checkboxes.

    Args:
        body: Pull-request description.
        template: Template text supplied by the caller; ``None`` if absent.
        enabled: Opt-in policy toggle.
        fail_on_violation: Escalate the warning to high severity.
        check_required_boxes: Enforce only boxes explicitly annotated
            ``<!-- required -->``, never ordinary optional checkboxes.

    Returns:
        One actionable warning with deterministic evidence, or ``None``.
        The function performs no repository or network I/O.
    """

    if not enabled or not template or not template.strip():
        return None

    required_sections = [
        (heading, content)
        for heading, content in _sections(template)
        if _OPTIONAL.search(heading) is None
    ]
    body_sections: dict[str, list[str]] = {}
    for heading, content in _sections(body):
        body_sections.setdefault(_normalize(heading), []).append(content)

    missing: list[str] = []
    empty: list[str] = []
    for heading, scaffold in required_sections:
        candidates = body_sections.get(_normalize(heading))
        if not candidates:
            missing.append(heading)
        elif not any(_section_filled(content, scaffold) for content in candidates):
            empty.append(heading)

    unchecked: list[str] = []
    if check_required_boxes:
        checked = _checked_checkbox_labels(body)
        unchecked = [
            label
            for label in _required_checkbox_labels(template)
            if _normalize(label) not in checked
        ]

    if not (missing or empty or unchecked):
        return None

    severity: WarningSeverity = "high" if fail_on_violation else "medium"
    return EngineWarning(
        code=WARN_CODE_PR_TEMPLATE_NOT_FOLLOWED,
        severity=severity,
        message=(
            "PR description does not follow the repository template: "
            f"{len(missing)} missing sections, {len(empty)} empty sections, "
            f"{len(unchecked)} unchecked required boxes."
        ),
        evidence={
            "missing_sections": missing,
            "empty_sections": empty,
            "unchecked_required": unchecked,
        },
    )


__all__ = [
    "WARN_CODE_PR_TEMPLATE_NOT_FOLLOWED",
    "pr_template_warning",
]
