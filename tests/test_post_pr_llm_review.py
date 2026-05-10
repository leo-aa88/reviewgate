"""Tests for the inline-comment helpers in ``scripts/post_pr_llm_review.py``.

Covers the helpers flagged by the AI reviewer on PR #81 as introducing
non-trivial behavior without direct test coverage:

* :func:`_split_inline_comments` -- partitioning model output into
  GitHub-anchored comments vs demoted general comments based on the
  parsed diff index.
* :func:`_format_inline_body` -- severity label, leading-marker strip,
  trailing-whitespace strip, quoted-line truncation.

The script lives in ``scripts/`` (not in the importable ``reviewgate``
package) and imports its sibling :mod:`_pr_review_llm` by bare name, so
the test inserts ``scripts/`` at the head of ``sys.path`` before
importing. We work with the helpers directly rather than mocking the
full ``_process_pr`` HTTP flow because the helpers are the only behavior
the diff actually adds; ``_process_pr`` is GitHub-API plumbing that
would require a much bigger HTTP fake to test honestly and would not
exercise additional logic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import post_pr_llm_review as ppr  # noqa: E402  (sys.path setup above)
from _pr_review_llm import parse_diff_right_side  # noqa: E402

if TYPE_CHECKING:
    from _pr_review_llm import DiffIndex, JsonValue


def _diff_index_with(path: str, lines: set[int]) -> DiffIndex:
    """Build a minimal :data:`DiffIndex` mapping path -> changed-line set."""

    return {path: lines}


# ---------------------------------------------------------------------------
# _format_inline_body
# ---------------------------------------------------------------------------


def test_format_inline_body_strips_leading_diff_marker() -> None:
    out = ppr._format_inline_body(
        body="Use a constant.",
        severity="must",
        quoted_line="+    return 42",
    )
    assert "    return 42" in out
    assert "+    return 42" not in out


def test_format_inline_body_handles_minus_and_space_markers() -> None:
    minus = ppr._format_inline_body(
        body="Reverted.", severity="should", quoted_line="-old line"
    )
    space = ppr._format_inline_body(
        body="Reverted.", severity="should", quoted_line=" context"
    )
    assert "old line" in minus and "-old line" not in minus
    assert "context" in space and " context\n" not in space


def test_format_inline_body_strips_trailing_whitespace_and_newline() -> None:
    out = ppr._format_inline_body(
        body="x", severity="nit", quoted_line="+ token   \n"
    )
    assert "token   " not in out
    assert "token\n```" in out


def test_format_inline_body_truncates_long_quoted_lines() -> None:
    long_line = "+" + ("x" * (ppr.QUOTED_LINE_DISPLAY_LIMIT + 50))
    out = ppr._format_inline_body(
        body="long", severity="must", quoted_line=long_line
    )
    assert "\u2026" in out
    quoted_block = out.split("```\n", 1)[1].rsplit("\n```", 1)[0]
    assert len(quoted_block) == ppr.QUOTED_LINE_DISPLAY_LIMIT + 1


def test_format_inline_body_uses_severity_label_from_table() -> None:
    out = ppr._format_inline_body(
        body="x", severity="must", quoted_line="+y"
    )
    assert out.startswith("**")
    assert "x" in out


def test_format_inline_body_falls_back_to_raw_severity_for_unknown_value() -> None:
    out = ppr._format_inline_body(
        body="x", severity="catastrophic", quoted_line="+y"
    )
    assert out.startswith("**catastrophic.**")


# ---------------------------------------------------------------------------
# _split_inline_comments
# ---------------------------------------------------------------------------


def _entry(
    *,
    path: str = "src/reviewgate/core/foo.py",
    line: int = 10,
    severity: str = "must",
    body: str = "Replace magic literal.",
    quoted: str = "+    return 42",
) -> JsonValue:
    return {
        "path": path,
        "line": line,
        "severity": severity,
        "body": body,
        "quoted_line": quoted,
    }


def test_split_inline_keeps_anchored_comment_in_valid_bucket() -> None:
    diff = _diff_index_with("src/reviewgate/core/foo.py", {10, 11})
    valid, demoted = ppr._split_inline_comments([_entry()], diff)

    assert demoted == []
    assert len(valid) == 1
    assert valid[0]["path"] == "src/reviewgate/core/foo.py"
    assert valid[0]["line"] == 10
    assert valid[0]["side"] == "RIGHT"
    assert "Replace magic literal." in str(valid[0]["body"])


def test_split_inline_demotes_when_line_outside_diff() -> None:
    diff = _diff_index_with("src/reviewgate/core/foo.py", {11, 12})
    valid, demoted = ppr._split_inline_comments([_entry(line=10)], diff)

    assert valid == []
    assert len(demoted) == 1
    assert demoted[0]["severity"] == "must"
    assert "src/reviewgate/core/foo.py:10" in str(demoted[0]["body"])
    assert "anchor not found in diff" in str(demoted[0]["body"])


def test_split_inline_demotes_when_path_outside_diff() -> None:
    diff = _diff_index_with("other.py", {10})
    valid, demoted = ppr._split_inline_comments([_entry()], diff)

    assert valid == []
    assert len(demoted) == 1


def test_split_inline_normalizes_a_b_path_prefix_before_anchor_check() -> None:
    diff = _diff_index_with("src/reviewgate/core/foo.py", {10})
    valid, _ = ppr._split_inline_comments(
        [_entry(path="b/src/reviewgate/core/foo.py")], diff
    )

    assert len(valid) == 1
    assert valid[0]["path"] == "src/reviewgate/core/foo.py"


@pytest.mark.parametrize(
    "bad_entry",
    [
        "not-a-dict",
        {"path": 123, "line": 10, "severity": "must", "body": "x", "quoted_line": "+y"},
        {"path": "p", "line": "10", "severity": "must", "body": "x", "quoted_line": "+y"},
        {"path": "p", "line": 10, "severity": 1, "body": "x", "quoted_line": "+y"},
        {"path": "p", "line": 10, "severity": "must", "body": None, "quoted_line": "+y"},
        {"path": "p", "line": 10, "severity": "must", "body": "x", "quoted_line": 42},
    ],
)
def test_split_inline_demotes_malformed_entries_instead_of_dropping(
    bad_entry: JsonValue,
) -> None:
    """Malformed entries must surface in ``demoted`` rather than vanish.

    The bot reviewer flagged silent ``continue`` here as a data-loss path:
    a malformed model output would erase a finding without any trace.
    Each malformed entry now produces exactly one demoted general comment
    so the human reviewer can see something went wrong.
    """

    diff = _diff_index_with("p", {10})
    valid, demoted = ppr._split_inline_comments([bad_entry], diff)

    assert valid == []
    assert len(demoted) == 1
    assert "malformed inline comment" in str(demoted[0]["body"])


def test_split_inline_partitions_mixed_batch_correctly() -> None:
    diff = _diff_index_with("src/foo.py", {5, 6})
    inline: list[JsonValue] = [
        _entry(path="src/foo.py", line=5, body="hit"),
        _entry(path="src/foo.py", line=99, body="miss-line"),
        _entry(path="src/bar.py", line=5, body="miss-path"),
    ]
    valid, demoted = ppr._split_inline_comments(inline, diff)

    assert [str(v["body"]).split("\n", 1)[0] for v in valid] == ["**Must-fix.** hit"]
    demoted_bodies = [str(d["body"]) for d in demoted]
    assert any("src/foo.py:99" in b for b in demoted_bodies)
    assert any("src/bar.py:5" in b for b in demoted_bodies)


# ---------------------------------------------------------------------------
# _list_paginated regression -- format-bug fix
# ---------------------------------------------------------------------------


def test_list_paginated_does_not_format_braces_in_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for the f909594 paginator fix: literal ``{x}`` in a URL
    must not be interpreted as a :py:meth:`str.format` field."""

    base = "https://api.example.com/q?per_page=2&filter={weird}"
    seen: list[str] = []

    def fake_http_json(
        method: str,
        url: str,
        token: str,
        *,
        accept: str = "application/vnd.github+json",
        body: ppr.JsonObject | None = None,
    ) -> ppr.JsonValue:
        seen.append(url)
        return []

    monkeypatch.setattr(ppr, "_http_json", fake_http_json)

    out = ppr._list_paginated(base, "tok", page_size=2)

    assert out == []
    assert seen == ["https://api.example.com/q?per_page=2&filter={weird}&page=1"]


def test_list_paginated_uses_question_mark_when_base_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def capture(
        method: str,
        url: str,
        token: str,
        *,
        accept: str = "application/vnd.github+json",
        body: ppr.JsonObject | None = None,
    ) -> ppr.JsonValue:
        seen.append(url)
        return []

    monkeypatch.setattr(ppr, "_http_json", capture)

    ppr._list_paginated("https://api.example.com/q", "tok", page_size=2)
    assert seen == ["https://api.example.com/q?page=1"]


# ---------------------------------------------------------------------------
# _post_pr_review event allowlist
# ---------------------------------------------------------------------------


def test_post_pr_review_rejects_unknown_event_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    def fake_http_json(*args: object, **kwargs: object) -> ppr.JsonValue:
        called.append("http")
        return None

    monkeypatch.setattr(ppr, "_http_json", fake_http_json)

    with pytest.raises(RuntimeError, match="Refusing to post"):
        ppr._post_pr_review(
            "owner",
            "repo",
            1,
            "tok",
            head_sha="deadbeef",
            body="b",
            event="APPROVE",
            comments=[],
        )
    assert called == []


# ---------------------------------------------------------------------------
# parse_diff_right_side -- branch coverage for hunk-walker edge cases
# ---------------------------------------------------------------------------


def test_parse_diff_indexes_added_and_context_lines() -> None:
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,3 @@\n"
        " context_a\n"
        "+added_b\n"
        " context_c\n"
    )
    index = parse_diff_right_side(diff)
    assert index == {"foo.py": {1, 2, 3}}


def test_parse_diff_skips_pure_deletion_to_dev_null() -> None:
    diff = (
        "diff --git a/dead.py b/dead.py\n"
        "deleted file mode 100644\n"
        "--- a/dead.py\n"
        "+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n"
        "-dead_a\n"
        "-dead_b\n"
        "-dead_c\n"
    )
    assert parse_diff_right_side(diff) == {}


def test_parse_diff_treats_blank_body_line_as_context() -> None:
    """A literal empty line inside a hunk is a context line on both sides.

    Without a leading marker character the walker must still advance the
    RIGHT counter and record the line as anchorable, otherwise inline
    comments on blank-line context targets get rejected.
    """

    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line_one\n"
        "\n"
        " line_three\n"
    )
    assert parse_diff_right_side(diff) == {"foo.py": {1, 2, 3}}


def test_parse_diff_skips_no_newline_at_end_of_file_marker() -> None:
    """``\\ No newline at end of file`` is metadata; do not advance counter."""

    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
        "\\ No newline at end of file\n"
    )
    assert parse_diff_right_side(diff) == {"foo.py": {1}}


def test_parse_diff_terminates_hunk_on_unknown_marker() -> None:
    """An unrecognised first character ends the current hunk cleanly.

    Anything that is not ``+ ``, ``- ``, ``  ``, or ``\\ `` is treated as
    the start of a new file-level section (e.g. ``diff --git`` of the
    next file). The walker must drop ``in_hunk`` so post-hunk noise does
    not get indexed as anchorable lines.
    """

    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,2 @@\n"
        " line_one\n"
        "+line_two\n"
        "garbage_after_hunk_should_terminate\n"
        "+also_after_terminator_should_be_ignored\n"
    )
    assert parse_diff_right_side(diff) == {"foo.py": {1, 2}}


def test_parse_diff_handles_path_without_b_prefix() -> None:
    diff = (
        "diff --git foo.py foo.py\n"
        "--- foo.py\n"
        "+++ foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "+only\n"
    )
    assert parse_diff_right_side(diff) == {"foo.py": {1}}


def test_parse_diff_skips_hunk_when_no_current_path() -> None:
    diff = "@@ -1,1 +1,1 @@\n+orphan\n"
    assert parse_diff_right_side(diff) == {}


def test_post_pr_review_accepts_allowlisted_events(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[ppr.JsonObject] = []

    def fake_http_json(
        method: str,
        url: str,
        token: str,
        *,
        accept: str = "application/vnd.github+json",
        body: ppr.JsonObject | None = None,
    ) -> ppr.JsonValue:
        assert body is not None
        called.append(body)
        return {}

    monkeypatch.setattr(ppr, "_http_json", fake_http_json)

    for event in ("COMMENT", "REQUEST_CHANGES"):
        ppr._post_pr_review(
            "owner",
            "repo",
            1,
            "tok",
            head_sha="deadbeef",
            body="b",
            event=event,
            comments=[],
        )

    assert [c["event"] for c in called] == ["COMMENT", "REQUEST_CHANGES"]
