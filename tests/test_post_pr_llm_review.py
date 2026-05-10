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

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import post_pr_llm_review as ppr  # noqa: E402  (sys.path setup above)
from _pr_review_llm import (  # noqa: E402
    _is_json_object,
    _is_json_value,
    parse_diff_right_side,
)

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


def test_list_paginated_raises_on_non_list_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a non-list payload must NOT terminate pagination silently.

    If GitHub returns an error envelope (or any unexpected shape) that
    leaks past ``_http_json``, treating it as "end of pagination" would
    let ``_already_reviewed`` miss an existing dedup marker and post
    duplicate reviews on the same head SHA. The helper now raises so
    the failure is loud.
    """

    def fake_http_json(*args: object, **kwargs: object) -> ppr.JsonValue:
        return {"message": "Not Found"}

    monkeypatch.setattr(ppr, "_http_json", fake_http_json)

    with pytest.raises(RuntimeError, match="Expected list from"):
        ppr._list_paginated(
            "https://api.example.com/q?per_page=2", "tok", page_size=2
        )


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


# ---------------------------------------------------------------------------
# _is_json_value / _is_json_object -- TypeGuard for the OpenAI return contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        0,
        1.5,
        "hi",
        [1, "x", None],
        {"a": 1, "b": [True, {"c": "d"}]},
        {},
        [],
    ],
)
def test_is_json_value_accepts_well_formed_json(value: object) -> None:
    assert _is_json_value(value) is True


@pytest.mark.parametrize(
    "value",
    [
        {1: "non-str-key"},
        {"k": {1, 2}},
        {"k": object()},
        [object()],
        b"bytes-are-not-json",
    ],
)
def test_is_json_value_rejects_non_json_shapes(value: object) -> None:
    assert _is_json_value(value) is False


def test_is_json_object_requires_top_level_dict_with_str_keys() -> None:
    assert _is_json_object({"k": "v"}) is True
    assert _is_json_object([1, 2, 3]) is False
    assert _is_json_object("scalar") is False
    assert _is_json_object({1: "non-str-key"}) is False


# ---------------------------------------------------------------------------
# _already_reviewed -- dedup across both issue-comment and review sources
# ---------------------------------------------------------------------------


def test_already_reviewed_finds_marker_in_review_body() -> None:
    """Regression for the dedup path that scans the Reviews API.

    Older revisions of this script posted the head-SHA marker as an
    issue comment; the current revision posts it as a review summary.
    Dedup must succeed against either source so we do not spam
    duplicate reviews on the same head SHA.
    """

    head_sha = "deadbeef0123456"
    marker = ppr._marker(head_sha)

    assert (
        ppr._already_reviewed(
            issue_comments=[],
            reviews=[{"body": f"Some prose {marker} more prose"}],
            head_sha=head_sha,
        )
        is True
    )


def test_already_reviewed_finds_marker_in_issue_comment_body() -> None:
    head_sha = "deadbeef0123456"
    marker = ppr._marker(head_sha)

    assert (
        ppr._already_reviewed(
            issue_comments=[{"body": marker}],
            reviews=[],
            head_sha=head_sha,
        )
        is True
    )


def test_already_reviewed_returns_false_when_marker_absent() -> None:
    head_sha = "deadbeef0123456"
    other = ppr._marker("cafebabe9999999")

    assert (
        ppr._already_reviewed(
            issue_comments=[{"body": "no marker here"}],
            reviews=[{"body": other}],
            head_sha=head_sha,
        )
        is False
    )


def test_already_reviewed_ignores_non_string_bodies() -> None:
    head_sha = "deadbeef0123456"
    assert (
        ppr._already_reviewed(
            issue_comments=[{"body": None}, {}],
            reviews=[{"body": 42}, {"body": ["x"]}],
            head_sha=head_sha,
        )
        is False
    )


# ---------------------------------------------------------------------------
# _process_pr orchestration -- happy path with stubbed GitHub + OpenAI
# ---------------------------------------------------------------------------


def test_process_pr_posts_review_with_anchored_and_demoted_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end smoke for ``_process_pr``: stub all I/O and assert the
    Reviews API payload carries the right ``event``, ``commit_id``,
    ``body``, and ``comments`` array.

    Covers two model-output shapes in one shot:

    * a valid inline anchor (``foo.py:2``) that lands in the
      ``comments`` payload as a ``side: RIGHT`` entry, and
    * an out-of-diff anchor (``foo.py:99``) that gets demoted into the
      review body rather than silently dropped.
    """

    head_sha = "deadbeef0123456789abcdef0000000000000000"
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,2 @@\n"
        " line_one\n"
        "+line_two\n"
    )

    posted: dict[str, ppr.JsonObject] = {}

    def fake_http_json(
        method: str,
        url: str,
        token: str,
        *,
        accept: str = "application/vnd.github+json",
        body: ppr.JsonObject | None = None,
    ) -> ppr.JsonValue:
        if method == "GET" and url.endswith("/pulls/7"):
            return {"head": {"sha": head_sha, "repo": {"full_name": "o/r"}}}
        if method == "POST" and url.endswith("/pulls/7/reviews"):
            assert body is not None
            posted["payload"] = body
            return {}
        raise AssertionError(f"unexpected HTTP call: {method} {url}")

    def fake_http_text(
        method: str, url: str, token: str, *, accept: str
    ) -> str:
        assert "diff" in accept
        return diff_text

    def fake_list_issue_comments(*args: object, **kwargs: object) -> list[ppr.JsonObject]:
        return []

    def fake_list_pr_reviews(*args: object, **kwargs: object) -> list[ppr.JsonObject]:
        return []

    def fake_call_openai_review(
        diff_text_arg: str,
        *,
        repo: str,
        pr_number: int,
        diff_index: object,
    ) -> ppr.JsonObject:
        return {
            "verdict": "request_changes",
            "summary": "Found one issue.",
            "inline_comments": [
                {
                    "path": "foo.py",
                    "line": 2,
                    "severity": "must",
                    "body": "Use a constant.",
                    "quoted_line": "+line_two",
                },
                {
                    "path": "foo.py",
                    "line": 99,
                    "severity": "should",
                    "body": "Out of diff.",
                    "quoted_line": "+missing",
                },
            ],
            "general_comments": [],
        }

    monkeypatch.setattr(ppr, "_http_json", fake_http_json)
    monkeypatch.setattr(ppr, "_http_text", fake_http_text)
    monkeypatch.setattr(ppr, "_list_issue_comments", fake_list_issue_comments)
    monkeypatch.setattr(ppr, "_list_pr_reviews", fake_list_pr_reviews)
    monkeypatch.setattr(ppr, "call_openai_review", fake_call_openai_review)
    monkeypatch.setenv("OPENAI_API_KEY", "stub-key")

    result = ppr._process_pr("o", "r", "o/r", 7, "tok")

    assert result.startswith("posted request_changes review for deadbee")
    payload = posted["payload"]
    assert payload["commit_id"] == head_sha
    assert payload["event"] == "REQUEST_CHANGES"
    body_str = str(payload["body"])
    assert ppr._marker(head_sha) in body_str
    assert "Out of diff." in body_str  # demoted entry surfaces in body
    comments = payload["comments"]
    assert isinstance(comments, list)
    assert len(comments) == 1
    only = comments[0]
    assert isinstance(only, dict)
    assert only["path"] == "foo.py"
    assert only["line"] == 2
    assert only["side"] == "RIGHT"
    assert "Use a constant." in str(only["body"])


def test_process_pr_still_posts_marker_only_review_when_no_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty-findings runs MUST still post (marker carries dedup state).

    Skipping the post here would mean the next CI iteration on the same
    head SHA re-runs the LLM (and pays for tokens) because no marker
    was left in the Reviews API to satisfy ``_already_reviewed``.
    The intended behavior is therefore to post a `COMMENT` review with
    the marker and an empty `comments` array; this test locks that in
    so a future "skip on empty" optimization cannot quietly regress
    dedup.
    """

    head_sha = "feedface" + "0" * 32
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        " line_one\n"
    )

    posted: dict[str, ppr.JsonObject] = {}

    def fake_http_json(
        method: str,
        url: str,
        token: str,
        *,
        accept: str = "application/vnd.github+json",
        body: ppr.JsonObject | None = None,
    ) -> ppr.JsonValue:
        if method == "GET":
            return {"head": {"sha": head_sha, "repo": {"full_name": "o/r"}}}
        assert body is not None
        posted["payload"] = body
        return {}

    monkeypatch.setattr(ppr, "_http_json", fake_http_json)
    monkeypatch.setattr(ppr, "_http_text", lambda *a, **kw: diff_text)
    monkeypatch.setattr(ppr, "_list_issue_comments", lambda *a, **kw: [])
    monkeypatch.setattr(ppr, "_list_pr_reviews", lambda *a, **kw: [])
    monkeypatch.setattr(
        ppr,
        "call_openai_review",
        lambda *a, **kw: {
            "verdict": "comment",
            "summary": "no findings",
            "inline_comments": [],
            "general_comments": [],
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "stub")

    result = ppr._process_pr("o", "r", "o/r", 11, "tok")

    assert result.startswith("posted comment review for feedfac")
    payload = posted["payload"]
    assert payload["event"] == "COMMENT"
    assert payload["commit_id"] == head_sha
    assert ppr._marker(head_sha) in str(payload["body"])
    assert payload["comments"] == []


def test_process_pr_skips_draft_pull_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Draft PRs must not consume an OpenAI quota or post a review.

    The module docstring lists ``ready_for_review`` as a trigger, so a
    draft PR reaching ``_process_pr`` (e.g. via the schedule trigger
    that catches missed webhooks) should short-circuit. Without the
    ``item["draft"]`` check, the bot would post on every draft sync.
    """

    def fake_http_json(
        method: str,
        url: str,
        token: str,
        *,
        accept: str = "application/vnd.github+json",
        body: ppr.JsonObject | None = None,
    ) -> ppr.JsonValue:
        assert url.endswith("/pulls/9")
        return {
            "head": {"sha": "0" * 40, "repo": {"full_name": "o/r"}},
            "draft": True,
        }

    monkeypatch.setattr(ppr, "_http_json", fake_http_json)

    assert ppr._process_pr("o", "r", "o/r", 9, "tok") == "skip: draft PR"


def test_process_pr_does_not_skip_when_draft_flag_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative case: ``"draft": false`` (the common path) must not skip.

    Pairs with ``test_process_pr_skips_draft_pull_request`` so the
    draft branch is exercised honestly: the skip happens iff the field
    is truthy. The dedup ``_already_reviewed`` returns True here so we
    short-circuit before any LLM call, keeping the test focused on the
    draft branch.
    """

    head_sha = "abc12340" + "0" * 32

    def fake_http_json(
        method: str,
        url: str,
        token: str,
        *,
        accept: str = "application/vnd.github+json",
        body: ppr.JsonObject | None = None,
    ) -> ppr.JsonValue:
        return {
            "head": {"sha": head_sha, "repo": {"full_name": "o/r"}},
            "draft": False,
        }

    monkeypatch.setattr(ppr, "_http_json", fake_http_json)
    monkeypatch.setattr(
        ppr,
        "_list_issue_comments",
        lambda *a, **kw: [{"body": ppr._marker(head_sha)}],
    )
    monkeypatch.setattr(ppr, "_list_pr_reviews", lambda *a, **kw: [])

    assert ppr._process_pr("o", "r", "o/r", 9, "tok").startswith("skip: already reviewed")


# ---------------------------------------------------------------------------
# call_openai_review -- failure-path coverage for malformed responses
# ---------------------------------------------------------------------------


class _StubResponse:
    """Minimal context-manager stand-in for ``urlopen``'s return value."""

    def __init__(self, payload: object) -> None:
        self._raw = json.dumps(payload).encode()

    def __enter__(self) -> _StubResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def _stub_urlopen(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    import _pr_review_llm as llm

    def fake_urlopen(
        req: object, timeout: float = 0, context: object = None
    ) -> _StubResponse:
        return _StubResponse(payload)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("OPENAI_API_KEY", "stub")


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({}, "Unexpected OpenAI response shape"),
        ({"choices": []}, "Unexpected OpenAI response shape"),
        ({"choices": [{}]}, "Unexpected OpenAI response shape"),
        ({"choices": [{"message": {}}]}, "Unexpected OpenAI response shape"),
    ],
)
def test_call_openai_review_raises_on_missing_content_field(
    monkeypatch: pytest.MonkeyPatch, payload: object, match: str
) -> None:
    from _pr_review_llm import call_openai_review

    _stub_urlopen(monkeypatch, payload)
    with pytest.raises(RuntimeError, match=match):
        call_openai_review("diff", repo="o/r", pr_number=1, diff_index={})


def test_call_openai_review_raises_on_non_json_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-JSON content must surface as ``RuntimeError`` to honor the docstring.

    Previously a malformed response leaked a raw ``json.JSONDecodeError``,
    which contradicts the documented ``Raises: RuntimeError`` contract on
    :func:`call_openai_review`. The wrapper re-raises with the offending
    content in the message.
    """

    from _pr_review_llm import call_openai_review

    _stub_urlopen(
        monkeypatch,
        {"choices": [{"message": {"content": "not-json-just-prose"}}]},
    )
    with pytest.raises(RuntimeError, match="invalid JSON content"):
        call_openai_review("diff", repo="o/r", pr_number=1, diff_index={})


@pytest.mark.parametrize(
    "content",
    ["[1, 2, 3]", '"scalar"', "42", "null"],
)
def test_call_openai_review_raises_when_parsed_is_not_json_object(
    monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    from _pr_review_llm import call_openai_review

    _stub_urlopen(
        monkeypatch, {"choices": [{"message": {"content": content}}]}
    )
    with pytest.raises(RuntimeError, match="non-JsonObject content"):
        call_openai_review("diff", repo="o/r", pr_number=1, diff_index={})


def test_call_openai_review_returns_validated_jsonobject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from _pr_review_llm import call_openai_review

    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "verdict": "comment",
                            "summary": "ok",
                            "inline_comments": [],
                            "general_comments": [],
                        }
                    )
                }
            }
        ]
    }
    _stub_urlopen(monkeypatch, payload)
    out = call_openai_review("diff", repo="o/r", pr_number=1, diff_index={})
    assert out["verdict"] == "comment"
    assert out["inline_comments"] == []


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
