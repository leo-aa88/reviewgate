"""Tests for :mod:`reviewgate.core.linked_issue` against \u00a710.10.

Locks every \u00a710.10 reference form (positive + negative tables), the
title-vs-body search surface, the policy-toggle behaviour, and the
warning shape so a future regex change cannot silently drop a form or
introduce noise.
"""

from __future__ import annotations

import pytest

from reviewgate.core.linked_issue import (
    WARN_CODE_MISSING_LINKED_ISSUE,
    find_issue_references,
    linked_issue_warning,
)


# --- positive matches per \u00a710.10 form -----------------------------------


@pytest.mark.parametrize(
    ("body", "expected_substring"),
    [
        pytest.param("Closes #123", "#123", id="bare-numeric-after-keyword"),
        pytest.param("See (#42) for context", "#42", id="bare-numeric-in-parens"),
        pytest.param("Fixes leo-aa88/repo#7", "leo-aa88/repo#7", id="cross-repo-numeric"),
        pytest.param("Tracks GH-9", "GH-9", id="gh-prefixed"),
        pytest.param("tracks gh-9", "gh-9", id="gh-prefixed-lowercase"),
        pytest.param("Closes JIRA-123", "JIRA-123", id="external-id-jira"),
        pytest.param("Closes ABC-1", "ABC-1", id="external-id-short"),
        pytest.param(
            "https://my-org.atlassian.net/browse/PROJ-42 for context",
            "https://my-org.atlassian.net/browse/PROJ-42",
            id="jira-url",
        ),
        pytest.param(
            "https://linear.app/acme/issue/ENG-7",
            "https://linear.app/acme/issue/ENG-7",
            id="linear-url",
        ),
        pytest.param(
            "https://github.com/leo-aa88/reviewgate/issues/12",
            "https://github.com/leo-aa88/reviewgate/issues/12",
            id="github-issue-url",
        ),
        pytest.param(
            "https://github.com/leo-aa88/reviewgate/pull/77",
            "https://github.com/leo-aa88/reviewgate/pull/77",
            id="github-pull-url",
        ),
    ],
)
def test_find_issue_references_matches_design_doc_forms(
    body: str,
    expected_substring: str,
) -> None:
    """Every \u00a710.10 reference form is detected at least once.

    We assert containment rather than equality so future evidence
    additions (e.g. surrounding context) do not break this table.
    """

    refs = find_issue_references("PR title", body)
    assert any(expected_substring in r for r in refs), refs


# --- title-vs-body search surface -----------------------------------------


def test_find_issue_references_searches_title_too() -> None:
    """\u00a710.10: detection runs over both title and body."""

    refs = find_issue_references("Fixes #5: tighten validation", "")
    assert any("#5" in r for r in refs)


def test_find_issue_references_combines_title_and_body() -> None:
    """References from either field are surfaced."""

    refs = find_issue_references("Closes ABC-1", "See #2 for follow-up.")
    flat = " ".join(refs)
    assert "ABC-1" in flat
    assert "#2" in flat


def test_find_issue_references_deduplicates_by_substring() -> None:
    """Repeated mentions of the same reference appear once."""

    refs = find_issue_references("Closes #7", "Also addresses #7 and #7.")
    assert refs.count("#7") == 1


# --- negative matches: substrings that must NOT trip the matcher ----------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("No related issues yet.", id="prose-no-numbers"),
        pytest.param("Bumps version from 1.2.3 to 1.2.4.", id="version-numbers"),
        pytest.param("Fixed bug in module foo-bar-7.", id="lowercase-id"),
        pytest.param("See A-1 for the legacy doc.", id="single-letter-prefix"),
        pytest.param(
            "Run with `--config=foo#1` flag.",
            id="hash-inside-shell-flag",
        ),
        pytest.param(
            "PR title without any issue references whatsoever.",
            id="long-prose",
        ),
    ],
)
def test_find_issue_references_does_not_match_unrelated_text(body: str) -> None:
    """Common prose / version numbers / single-letter IDs must not match."""

    assert find_issue_references("title", body) == []


# --- linked_issue_warning verdict ladder ----------------------------------


def test_warning_is_none_when_policy_disabled() -> None:
    """\u00a712: ``require_linked_issue=False`` silences the heuristic."""

    assert (
        linked_issue_warning(
            "no references at all",
            "still no references",
            require_linked_issue=False,
        )
        is None
    )


def test_warning_is_none_when_reference_present() -> None:
    """A single \u00a710.10 reference satisfies the policy check."""

    warning = linked_issue_warning(
        "Closes #42",
        "Just a fix.",
        require_linked_issue=True,
    )
    assert warning is None


def test_warning_emitted_when_required_but_missing() -> None:
    """No reference + policy on -> single medium warning."""

    warning = linked_issue_warning(
        "tweak validation",
        "Tightens up the user input checks.",
        require_linked_issue=True,
    )
    assert warning is not None
    assert warning.code == WARN_CODE_MISSING_LINKED_ISSUE
    assert warning.severity == "medium"
    assert warning.evidence["policy"] == "require_linked_issue"
    assert warning.evidence["patterns_checked"] >= 1


def test_warning_message_lists_example_forms() -> None:
    """Reviewers see concrete \u00a710.10 examples in the message."""

    warning = linked_issue_warning(
        "x", "y", require_linked_issue=True
    )
    assert warning is not None
    assert "#123" in warning.message
    assert "GH-123" in warning.message
    assert "ABC-123" in warning.message


def test_external_id_pattern_requires_two_uppercase_letters() -> None:
    """``A-1`` is too noisy; the matcher requires ``[A-Z][A-Z0-9]+`` prefix.

    Regression guard: a one-letter prefix must not satisfy the policy.
    """

    warning = linked_issue_warning("title", "See A-1.", require_linked_issue=True)
    assert warning is not None, "single-letter ID must not satisfy the policy"


def test_jira_url_with_uppercase_path_still_matches() -> None:
    """URL matching is case-insensitive (some tracker installs uppercase paths)."""

    refs = find_issue_references(
        "title",
        "Closes HTTPS://MY-ORG.ATLASSIAN.NET/BROWSE/PROJ-42 today.",
    )
    assert any("PROJ-42" in r for r in refs)
