"""Tests for ``reviewgate_action.post_comment`` (issue #26).

Covers the §13 PR-comment upsert flow:

* the §13 marker stays embedded across edits so the upsert lookup
  can re-find the bot's comment after a force-push or re-run,
* pagination (`Link: rel="next"`) is followed when the PR has
  more than one page of comments,
* the upsert decides between PATCH (existing marker found) and
  POST (no marker found),
* HTTP errors from the GitHub API surface as a clean
  ``RuntimeError`` rather than a raw ``urllib`` traceback.

All tests stay hermetic by injecting a stub opener mirroring the
fetch_pr test suite.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from reviewgate.core.engine import analyze
from reviewgate.core.schemas import EngineInput
from reviewgate_action import post_comment


# --- HTTP stub (mirrors test_fetch_pr style) -------------------------


class _StubResponse:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._buf = io.BytesIO(body)
        self.headers = headers

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self._buf.close()


class _StubOpener:
    """In-order recorder for HTTP exchanges.

    Each entry is ``(method, url_substring, status_or_body, headers)``.
    ``status_or_body`` is either a ``bytes`` payload (success) or an
    ``HTTPError`` instance (error). The opener pops entries in order
    and asserts the request matches; tests with branchy logic should
    spell out every expected call.
    """

    def __init__(
        self,
        responses: list[tuple[str, str, Any, dict[str, str]]],
    ) -> None:
        self._queue = list(responses)
        self.requests: list[urllib.request.Request] = []

    def open(
        self,
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> _StubResponse:
        assert self._queue, (
            f"_StubOpener exhausted before request {request.method} "
            f"{request.full_url}"
        )
        method, url_substring, payload, headers = self._queue.pop(0)
        assert request.method == method, (
            f"unexpected HTTP method: expected {method}, got {request.method}"
        )
        assert url_substring in request.full_url, (
            f"unexpected URL: expected substring {url_substring!r}, got "
            f"{request.full_url!r}"
        )
        self.requests.append(request)
        if isinstance(payload, urllib.error.HTTPError):
            raise payload
        assert isinstance(payload, bytes)
        return _StubResponse(payload, headers)


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _make_report():
    pass_input = {
        "pr": {
            "title": "Add fuzzy operator",
            "body": (
                "Implements the fuzzy operator across the search index, "
                "matching the behaviour described in linked issue #321."
            ),
            "author": "octocat",
            "base_branch": "main",
            "head_branch": "feat/fuzzy",
            "additions": 30,
            "deletions": 5,
            "changed_files": 2,
        },
        "files": [
            {
                "filename": "src/search/parser.py",
                "status": "modified",
                "additions": 20,
                "deletions": 5,
                "changes": 25,
            },
            {
                "filename": "src/search/operators.py",
                "status": "added",
                "additions": 10,
                "deletions": 0,
                "changes": 10,
            },
        ],
        "config": {},
    }
    return analyze(EngineInput.model_validate(pass_input))


# --- marker semantics -----------------------------------------------


def test_marker_constant_is_embedded_in_rendered_body() -> None:
    """`render_comment_body` must put the §13 marker first.

    The upsert lookup matches on the marker; placing it on the
    first non-empty line keeps the matcher cheap and the comment
    diff stable across re-runs.
    """

    report = _make_report()
    body = post_comment.render_comment_body(report, "## ReviewGate [PASS] `PASS`\n")
    assert body.startswith(post_comment.MARKER)
    assert "## ReviewGate" in body


# --- find_existing_comment_id ---------------------------------------


def test_find_existing_comment_id_returns_id_for_marker_match() -> None:
    opener = _StubOpener(
        [
            (
                "GET",
                "/issues/77/comments",
                _json_body(
                    [
                        {"id": 1, "body": "lgtm"},
                        {"id": 2, "body": f"{post_comment.MARKER}\nstale"},
                    ]
                ),
                {},
            )
        ]
    )
    found = post_comment.find_existing_comment_id(
        owner="o", repo="r", pull_number=77, token="t", opener=opener
    )
    assert found == 2


def test_find_existing_comment_id_returns_none_when_no_marker() -> None:
    opener = _StubOpener(
        [
            (
                "GET",
                "/issues/77/comments",
                _json_body([{"id": 1, "body": "lgtm"}]),
                {},
            )
        ]
    )
    assert (
        post_comment.find_existing_comment_id(
            owner="o", repo="r", pull_number=77, token="t", opener=opener
        )
        is None
    )


def test_find_existing_comment_id_follows_link_next_pagination() -> None:
    opener = _StubOpener(
        [
            (
                "GET",
                "/issues/3/comments",
                _json_body([{"id": 1, "body": "first page"}]),
                {"Link": '<https://api.github.com/x?page=2>; rel="next"'},
            ),
            (
                "GET",
                "api.github.com",
                _json_body(
                    [{"id": 2, "body": f"{post_comment.MARKER}\nfound"}]
                ),
                {},
            ),
        ]
    )
    assert (
        post_comment.find_existing_comment_id(
            owner="o", repo="r", pull_number=3, token="t", opener=opener
        )
        == 2
    )


def test_find_existing_comment_id_handles_legacy_marker_without_version() -> None:
    """A v1 marker today must still match the regex if we drop ``:v1``.

    Future refactors (e.g. dropping the version suffix) should not
    orphan existing comments. The regex accepts both shapes; this
    test pins that contract.
    """

    legacy_body = "<!-- reviewgate:marker -->\nold body"
    opener = _StubOpener(
        [
            (
                "GET",
                "/issues/9/comments",
                _json_body([{"id": 99, "body": legacy_body}]),
                {},
            )
        ]
    )
    assert (
        post_comment.find_existing_comment_id(
            owner="o", repo="r", pull_number=9, token="t", opener=opener
        )
        == 99
    )


def test_find_existing_comment_id_aborts_when_pagination_runaway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(post_comment, "_COMMENTS_MAX_PAGES", 2)
    opener = _StubOpener(
        [
            (
                "GET",
                "api.github.com",
                _json_body([]),
                {"Link": '<https://api.github.com/x?page=2>; rel="next"'},
            ),
            (
                "GET",
                "api.github.com",
                _json_body([]),
                {"Link": '<https://api.github.com/x?page=3>; rel="next"'},
            ),
        ]
    )
    with pytest.raises(RuntimeError, match="more than 2 pages"):
        post_comment.find_existing_comment_id(
            owner="o", repo="r", pull_number=1, token="t", opener=opener
        )


def test_find_existing_comment_id_raises_on_non_list_response() -> None:
    opener = _StubOpener(
        [
            (
                "GET",
                "/issues/77/comments",
                _json_body({"oops": True}),
                {},
            )
        ]
    )
    with pytest.raises(RuntimeError, match="unexpected /issues/77"):
        post_comment.find_existing_comment_id(
            owner="o", repo="r", pull_number=77, token="t", opener=opener
        )


# --- upsert_comment --------------------------------------------------


def test_upsert_comment_creates_when_marker_absent() -> None:
    report = _make_report()
    opener = _StubOpener(
        [
            ("GET", "/issues/12/comments", _json_body([]), {}),
            (
                "POST",
                "/issues/12/comments",
                _json_body({"id": 4242, "body": "..."}),
                {},
            ),
        ]
    )
    action, comment_id = post_comment.upsert_comment(
        owner="o",
        repo="r",
        pull_number=12,
        token="t",
        report=report,
        summary_md="## ReviewGate [PASS] `PASS`\n",
        opener=opener,
    )
    assert action == "created"
    assert comment_id == 4242
    posted = json.loads(opener.requests[1].data.decode("utf-8"))  # type: ignore[union-attr]
    assert posted["body"].startswith(post_comment.MARKER)


def test_upsert_comment_patches_when_marker_found() -> None:
    report = _make_report()
    opener = _StubOpener(
        [
            (
                "GET",
                "/issues/13/comments",
                _json_body(
                    [{"id": 555, "body": f"{post_comment.MARKER}\nstale"}]
                ),
                {},
            ),
            (
                "PATCH",
                "/issues/comments/555",
                _json_body({"id": 555, "body": "..."}),
                {},
            ),
        ]
    )
    action, comment_id = post_comment.upsert_comment(
        owner="o",
        repo="r",
        pull_number=13,
        token="t",
        report=report,
        summary_md="## ReviewGate [PASS] `PASS`\n",
        opener=opener,
    )
    assert action == "updated"
    assert comment_id == 555
    patched = json.loads(opener.requests[1].data.decode("utf-8"))  # type: ignore[union-attr]
    assert patched["body"].startswith(post_comment.MARKER)


def test_upsert_comment_translates_http_error_to_runtime_error() -> None:
    report = _make_report()
    opener = _StubOpener(
        [
            (
                "GET",
                "/issues/14/comments",
                urllib.error.HTTPError(
                    "https://api.github.com/repos/o/r/issues/14/comments",
                    403,
                    "Forbidden",
                    hdrs=None,  # type: ignore[arg-type]
                    fp=io.BytesIO(b"{}"),
                ),
                {},
            )
        ]
    )
    with pytest.raises(RuntimeError, match="HTTP 403 Forbidden"):
        post_comment.upsert_comment(
            owner="o",
            repo="r",
            pull_number=14,
            token="t",
            report=report,
            summary_md="x",
            opener=opener,
        )


def test_upsert_comment_raises_on_unexpected_post_response() -> None:
    report = _make_report()
    opener = _StubOpener(
        [
            ("GET", "/issues/15/comments", _json_body([]), {}),
            (
                "POST",
                "/issues/15/comments",
                _json_body({"oops": "no id"}),
                {},
            ),
        ]
    )
    with pytest.raises(RuntimeError, match="unexpected response shape"):
        post_comment.upsert_comment(
            owner="o",
            repo="r",
            pull_number=15,
            token="t",
            report=report,
            summary_md="x",
            opener=opener,
        )
