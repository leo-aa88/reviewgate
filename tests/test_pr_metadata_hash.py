"""Tests for :mod:`reviewgate.app.analysis.pr_metadata_hash` (issue #42)."""

from __future__ import annotations

from reviewgate.app.analysis.pr_metadata_hash import (
    build_pr_metadata_hash_payload,
    compute_pr_metadata_hash,
    normalize_text_for_pr_metadata_hash,
)


def test_normalize_collapses_whitespace_and_line_endings() -> None:
    raw = "  hello\r\nworld  \t  x  "
    assert normalize_text_for_pr_metadata_hash(raw) == "hello world x"


def test_normalize_strips_html_comments() -> None:
    text = "a <!-- hide --> b"
    assert normalize_text_for_pr_metadata_hash(text) == "a b"


def test_compute_pr_metadata_hash_stable_when_whitespace_differs_only_around_refs() -> None:
    """Whitespace collapse yields identical normalized bodies and sorted refs."""

    h1 = compute_pr_metadata_hash(
        title="x",
        body="see  #1  and  #2",
        base_branch="main",
    )
    h2 = compute_pr_metadata_hash(
        title="x",
        body="see #1 and #2",
        base_branch="main",
    )
    assert h1 == h2


def test_compute_pr_metadata_hash_ignores_meaningless_whitespace_only_body_edit() -> None:
    before = compute_pr_metadata_hash(
        title="t",
        body="hello   world",
        base_branch="main",
    )
    after = compute_pr_metadata_hash(
        title="t",
        body="hello world",
        base_branch="main",
    )
    assert before == after


def test_compute_pr_metadata_hash_changes_when_base_branch_changes() -> None:
    a = compute_pr_metadata_hash(title="t", body="", base_branch="main")
    b = compute_pr_metadata_hash(title="t", body="", base_branch="dev")
    assert a != b


def test_build_payload_sorted_keys_roundtrip_json() -> None:
    payload = build_pr_metadata_hash_payload(
        title="t",
        body="#99",
        base_branch="main",
    )
    assert list(payload.keys()) == sorted(payload.keys())
    assert payload["linked_issue_refs"] == ["#99"]

