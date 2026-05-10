"""Tests for :mod:`reviewgate.core.pr_body` against \u00a710.10.

Locks the four \u00a710.10 weak-body cases against the cleaning pipeline:

* empty / whitespace-only -> ``REASON_EMPTY``
* fewer than 80 meaningful chars -> ``REASON_INSUFFICIENT``
* "mostly template headings without content" -> falls out of the
  cleaning pipeline as ``REASON_INSUFFICIENT`` (heading text is kept
  but template scaffolding alone never reaches the threshold)
* substantive bodies emit no warning

Tests also pin the exact :func:`meaningful_text` cleaning behaviour so
a future regex change cannot silently inflate or deflate the count.
"""

from __future__ import annotations

import pytest

from reviewgate.core.pr_body import (
    MIN_MEANINGFUL_CHARS,
    REASON_EMPTY,
    REASON_INSUFFICIENT,
    WARN_CODE_WEAK_BODY,
    meaningful_char_count,
    meaningful_text,
    weak_body_warning,
)


# --- meaningful_text cleaning pipeline --------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("   \n\t  ", "", id="whitespace-only"),
        pytest.param("Hello world", "Hello world", id="plain-prose"),
        pytest.param("# Heading\n\nbody text", "Heading body text", id="strips-heading-prefix"),
        pytest.param("## Why\n## How\n", "Why How", id="multiple-headings-same-line-merged"),
        pytest.param(
            "<!-- hidden author guidance -->\nReal content here.",
            "Real content here.",
            id="strips-html-comments",
        ),
        pytest.param(
            "Top\n<!-- multi\nline\ncomment -->\nBottom",
            "Top Bottom",
            id="strips-multiline-html-comment",
        ),
        pytest.param(
            "---\nbody\n---\n",
            "body",
            id="strips-horizontal-rules",
        ),
        pytest.param(
            "- first\n- second\n* third\n+ fourth",
            "first second third fourth",
            id="strips-bullet-prefixes",
        ),
        pytest.param(
            "1. one\n2. two\n10. ten",
            "one two ten",
            id="strips-numbered-list-prefixes",
        ),
        pytest.param(
            "- [ ] todo item\n- [x] done item",
            "todo item done item",
            id="strips-checkbox-and-bullet",
        ),
        pytest.param(
            "> quoted text\n>> double quoted",
            "quoted text double quoted",
            id="strips-blockquote",
        ),
        pytest.param(
            "Line one.    Line\ttwo.",
            "Line one. Line two.",
            id="collapses-internal-whitespace",
        ),
    ],
)
def test_meaningful_text_strips_template_noise(body: str, expected: str) -> None:
    assert meaningful_text(body) == expected


def test_meaningful_char_count_ignores_whitespace() -> None:
    """Counts non-whitespace chars after cleaning, not raw length."""

    body = "## Heading\n\n- bullet item with text\n"
    assert meaningful_char_count(body) == len("Headingbulletitemwithtext")


# --- weak_body_warning verdict ladder ---------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("", id="empty-string"),
        pytest.param("   ", id="spaces"),
        pytest.param("\n\n\t\n", id="newlines-and-tabs"),
        pytest.param("<!-- hidden -->", id="html-comment-only"),
    ],
)
def test_empty_or_whitespace_body_emits_empty_reason(body: str) -> None:
    """\u00a710.10 case 1+2: empty / whitespace -> ``REASON_EMPTY``.

    Note: a body containing only an HTML comment is whitespace once the
    comment is stripped, so it falls into the same bucket.
    """

    warning = weak_body_warning(body)
    assert warning is not None
    assert warning.code == WARN_CODE_WEAK_BODY
    assert warning.severity == "medium"
    assert warning.evidence["reason"] == REASON_EMPTY
    assert warning.evidence["meaningful_chars"] == 0
    assert warning.evidence["threshold"] == MIN_MEANINGFUL_CHARS


def test_short_body_emits_insufficient_reason() -> None:
    """\u00a710.10 case 3: fewer than 80 meaningful chars -> insufficient."""

    body = "Quick fix for typo."
    warning = weak_body_warning(body)
    assert warning is not None
    assert warning.evidence["reason"] == REASON_INSUFFICIENT
    assert warning.evidence["meaningful_chars"] == len("Quickfixfortypo.")


def test_template_only_body_emits_insufficient_reason() -> None:
    """\u00a710.10 case 4: template scaffolding alone is insufficient.

    A body that contains only the standard ``## Summary / ## Test plan``
    template headings and unchecked checkboxes should not reach the 80
    meaningful-char threshold once the heading prefixes and checkbox
    markers are stripped.
    """

    body = (
        "<!-- describe the change below -->\n"
        "## Summary\n\n"
        "## Test plan\n\n"
        "- [ ]\n"
        "- [ ]\n"
        "---\n"
    )
    warning = weak_body_warning(body)
    assert warning is not None
    assert warning.evidence["reason"] == REASON_INSUFFICIENT
    assert warning.evidence["meaningful_chars"] < MIN_MEANINGFUL_CHARS


def test_substantive_body_emits_no_warning() -> None:
    """A body well above 80 meaningful chars produces no warning."""

    body = (
        "## Summary\n\n"
        "Adds a new endpoint for fetching user activity, including a "
        "Redis cache and a backfill script for historical data. "
        "Uses the existing auth middleware and emits the same metrics "
        "as the dashboard endpoint.\n\n"
        "## Test plan\n\n"
        "- [x] Unit tests for the new handler\n"
        "- [x] Manual smoke against staging\n"
    )
    assert weak_body_warning(body) is None


def test_body_at_exact_threshold_emits_no_warning() -> None:
    """\u00a710.10 threshold is inclusive lower bound: 80 chars is fine."""

    body = "x" * MIN_MEANINGFUL_CHARS
    assert meaningful_char_count(body) == MIN_MEANINGFUL_CHARS
    assert weak_body_warning(body) is None


def test_body_one_char_below_threshold_emits_warning() -> None:
    """One character below the threshold trips the WARN."""

    body = "x" * (MIN_MEANINGFUL_CHARS - 1)
    warning = weak_body_warning(body)
    assert warning is not None
    assert warning.evidence["reason"] == REASON_INSUFFICIENT
    assert warning.evidence["meaningful_chars"] == MIN_MEANINGFUL_CHARS - 1


def test_warning_message_includes_threshold_and_count() -> None:
    """Reviewers can reconstruct the decision from the message alone."""

    body = "tiny"
    warning = weak_body_warning(body)
    assert warning is not None
    assert "4" in warning.message
    assert str(MIN_MEANINGFUL_CHARS) in warning.message


def test_long_template_with_content_does_not_warn() -> None:
    """Template noise plus real prose above threshold is fine."""

    body = (
        "## Summary\n\n"
        + "Real meaningful prose. " * 5
        + "\n\n## Test plan\n\n- [ ] something\n"
    )
    assert meaningful_char_count(body) >= MIN_MEANINGFUL_CHARS
    assert weak_body_warning(body) is None
