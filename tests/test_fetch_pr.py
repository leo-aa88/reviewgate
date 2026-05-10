"""Tests for ``reviewgate_action.fetch_pr`` (issue #24).

The module is the open-source GitHub Action's I/O boundary: it reads
the GitHub REST API and emits a §10.1 :class:`EngineInput` JSON
document. These tests cover:

* The env-var contract (``GITHUB_TOKEN``, ``GITHUB_REPOSITORY``,
  ``GITHUB_EVENT_PATH``) and the documented failure messages.
* Pagination via the ``Link: rel="next"`` header (the CI of a real PR
  with hundreds of files relies on this).
* §10.1 mapping correctness, including the file-status normalization
  that bridges GitHub's wider status set onto the engine's closed
  Literal.
* End-to-end ``main`` invocation with a stubbed HTTP layer so the
  test stays hermetic (no real network, no flaky fixtures).
"""

from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path
from typing import Any, Final

import pytest

from reviewgate.core.schemas import EngineInput
from reviewgate_action import fetch_pr


# --- HTTP stub -------------------------------------------------------


class _StubResponse:
    """Minimal :class:`http.client.HTTPResponse` stand-in.

    Tests construct one per request; the stub mirrors the subset of
    the real urllib response surface that
    ``fetch_pr._http_get_json`` consumes (``read()``, the context
    manager, and the ``headers`` mapping).
    """

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
    """Drop-in :class:`urllib.request.OpenerDirector` for tests.

    Holds an ordered queue of ``(url_substring, response)`` pairs.
    Each ``open(req)`` call pops the next entry and returns its
    response, asserting the request URL matches the substring so a
    test fails loudly if the HTTP order drifts from what
    ``fetch_pr`` is supposed to emit.
    """

    def __init__(
        self,
        responses: list[tuple[str, bytes, dict[str, str]]],
    ) -> None:
        self._queue: list[tuple[str, bytes, dict[str, str]]] = list(responses)
        self.requests: list[urllib.request.Request] = []

    def open(
        self,
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> _StubResponse:
        assert self._queue, (
            f"_StubOpener exhausted before request {request.full_url}"
        )
        url_substring, body, headers = self._queue.pop(0)
        assert url_substring in request.full_url, (
            f"unexpected request order: expected URL containing "
            f"{url_substring!r}, got {request.full_url!r}"
        )
        self.requests.append(request)
        return _StubResponse(body, headers)


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


# --- env-var contract ------------------------------------------------


def test_required_env_raises_with_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing env vars surface as RuntimeError with the var name."""

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        fetch_pr._required_env("GITHUB_TOKEN")


def test_required_env_treats_empty_string_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty value behaves like a missing var (matches Actions semantics)."""

    monkeypatch.setenv("GITHUB_TOKEN", "")
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        fetch_pr._required_env("GITHUB_TOKEN")


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "owner",
        "owner/repo/extra",
        "owner repo",
        "owner/",
        "/repo",
    ],
)
def test_split_repo_rejects_malformed_slugs(slug: str) -> None:
    """``GITHUB_REPOSITORY`` must be exactly ``owner/repo``."""

    with pytest.raises(RuntimeError, match="GITHUB_REPOSITORY"):
        fetch_pr._split_repo(slug)


def test_split_repo_accepts_dotted_and_dashed_names() -> None:
    """Real repos use ``-``, ``_``, and ``.``; the validator must allow them."""

    assert fetch_pr._split_repo("leo-aa88/reviewgate.core_v2") == (
        "leo-aa88",
        "reviewgate.core_v2",
    )


def test_pull_number_from_event_reads_pull_request_block(tmp_path: Path) -> None:
    """The PR number comes from ``pull_request.number`` first.

    Both ``pull_request`` and ``pull_request_target`` webhooks deliver
    the PR resource under the ``pull_request`` key with ``number`` as a
    top-level field of that object. The synthetic payload here pins
    that contract; a realistic payload shape is exercised in
    :func:`test_pull_number_from_event_handles_realistic_pull_request_payload`.
    """

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"number": 42}, "number": 99}),
        encoding="utf-8",
    )
    assert fetch_pr._pull_number_from_event(str(event)) == 42


@pytest.mark.parametrize(
    "action_subtype",
    ["opened", "synchronize", "reopened", "edited"],
)
def test_pull_number_from_event_handles_realistic_pull_request_payload(
    tmp_path: Path, action_subtype: str
) -> None:
    """A realistic ``pull_request.<subtype>`` payload resolves the PR number.

    Mirrors the documented `pull_request` webhook shape (Action,
    `number`, nested `pull_request` resource with its own `number`,
    `head`, `base`, `user`). Pinning the resolution against this
    shape across the four subtypes the §14 example workflow listens
    on (`opened`, `synchronize`, `reopened`, `edited`) catches a
    regression where the resolver might prefer the top-level
    ``number`` (which exists on `pull_request` events too and equals
    the PR number) but lose the field for other PR-shaped events.
    """

    payload = {
        "action": action_subtype,
        "number": 137,
        "pull_request": {
            "number": 137,
            "title": "Refactor SearchIndex",
            "user": {"login": "octocat"},
            "head": {"ref": "feat/search"},
            "base": {"ref": "main"},
        },
    }
    event = tmp_path / "event.json"
    event.write_text(json.dumps(payload), encoding="utf-8")
    assert fetch_pr._pull_number_from_event(str(event)) == 137


def test_pull_number_from_event_handles_pull_request_target_payload(
    tmp_path: Path,
) -> None:
    """``pull_request_target`` events use the same payload shape.

    GitHub documents `pull_request_target` as carrying an identical
    payload to `pull_request`; the resolver must therefore work for
    that event too without a code branch.
    """

    payload = {
        "action": "synchronize",
        "number": 901,
        "pull_request": {
            "number": 901,
            "title": "Bump deps",
            "user": {"login": "renovate-bot"},
            "head": {"ref": "deps/quarterly"},
            "base": {"ref": "main"},
        },
    }
    event = tmp_path / "event.json"
    event.write_text(json.dumps(payload), encoding="utf-8")
    assert fetch_pr._pull_number_from_event(str(event)) == 901


def test_pull_number_from_event_falls_back_to_top_level(tmp_path: Path) -> None:
    """Some PR-shaped events only carry ``number`` at the top level."""

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"number": 7}), encoding="utf-8")
    assert fetch_pr._pull_number_from_event(str(event)) == 7


def test_pull_number_from_event_prefers_pull_request_block_over_top_level(
    tmp_path: Path,
) -> None:
    """A mismatched top-level ``number`` must lose to ``pull_request.number``.

    Real `pull_request` payloads carry the PR number in both places
    and they always agree, but pinning the precedence rule prevents
    a future divergence (e.g., a sub-event that reuses ``number`` for
    something else like a comment id) from silently changing behaviour.
    """

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"number": 999, "pull_request": {"number": 1}}),
        encoding="utf-8",
    )
    assert fetch_pr._pull_number_from_event(str(event)) == 1


def test_pull_number_from_event_skips_non_dict_pull_request_block(
    tmp_path: Path,
) -> None:
    """A null/scalar ``pull_request`` field must not crash; falls back."""

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": None, "number": 88}),
        encoding="utf-8",
    )
    assert fetch_pr._pull_number_from_event(str(event)) == 88


def test_pull_number_from_event_raises_on_non_pr_event(tmp_path: Path) -> None:
    """Non-PR events (e.g. ``push``) must fail loudly, not return -1."""

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"action": "synchronize"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="pull request number"):
        fetch_pr._pull_number_from_event(str(event))


def test_pull_number_from_event_raises_on_malformed_json(tmp_path: Path) -> None:
    """Malformed event payloads must surface a clear parse error."""

    event = tmp_path / "event.json"
    event.write_text("not-json::", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        fetch_pr._pull_number_from_event(str(event))


def test_pull_number_from_event_raises_when_missing_file(tmp_path: Path) -> None:
    """A missing event file must surface as RuntimeError, not OSError."""

    missing = tmp_path / "no-such-event.json"
    with pytest.raises(RuntimeError, match="GITHUB_EVENT_PATH"):
        fetch_pr._pull_number_from_event(str(missing))


# --- HTTP layer -------------------------------------------------------


def test_github_headers_include_required_fields() -> None:
    """Auth, API version, and content negotiation must all be set."""

    headers = fetch_pr._github_headers("ghp_test")
    assert headers["Authorization"] == "Bearer ghp_test"
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == fetch_pr._GITHUB_API_VERSION
    assert "User-Agent" in headers


def test_http_get_json_returns_parsed_body_and_headers() -> None:
    """The HTTP helper returns ``(body, lowercased headers)``."""

    opener = _StubOpener(
        [
            ("/pulls/1", _json_body({"hello": "world"}), {"Link": "<x>"}),
        ]
    )
    body, headers = fetch_pr._http_get_json(
        "https://api.github.com/repos/o/r/pulls/1",
        token="t",
        opener=opener,
    )
    assert body == {"hello": "world"}
    assert headers["link"] == "<x>"


def test_http_get_json_translates_http_error_to_runtime_error() -> None:
    """A ``urllib.error.HTTPError`` must surface as RuntimeError with URL."""

    import urllib.error

    class _ErrorOpener:
        def open(
            self,
            request: urllib.request.Request,
            timeout: float | None = None,
        ) -> _StubResponse:
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b"{}"),
            )

    with pytest.raises(RuntimeError, match="HTTP 403 Forbidden"):
        fetch_pr._http_get_json(
            "https://api.github.com/repos/o/r/pulls/1",
            token="t",
            opener=_ErrorOpener(),  # type: ignore[arg-type]
        )


def test_http_get_json_raises_on_non_json_body() -> None:
    """A non-JSON body (e.g. an HTML proxy page) must fail with context."""

    opener = _StubOpener([("/pulls/1", b"not json", {})])
    with pytest.raises(RuntimeError, match="not valid JSON"):
        fetch_pr._http_get_json(
            "https://api.github.com/repos/o/r/pulls/1",
            token="t",
            opener=opener,
        )


def test_next_url_returns_none_when_link_header_absent() -> None:
    assert fetch_pr._next_url({}) is None


def test_next_url_returns_none_when_link_has_no_next() -> None:
    headers = {"link": '<https://api.github.com/x?page=1>; rel="prev"'}
    assert fetch_pr._next_url(headers) is None


def test_next_url_extracts_url_from_compound_link_header() -> None:
    """GitHub combines ``next``, ``prev``, ``last`` in a single header."""

    headers = {
        "link": (
            '<https://api.github.com/x?page=2>; rel="next", '
            '<https://api.github.com/x?page=10>; rel="last"'
        )
    }
    assert fetch_pr._next_url(headers) == "https://api.github.com/x?page=2"


# --- pagination ------------------------------------------------------


def test_fetch_files_follows_link_next_until_exhausted() -> None:
    """The fetcher must concatenate every page reported by ``Link``."""

    page_one = [
        {"filename": f"p1/file_{i}.py", "status": "modified",
         "additions": 1, "deletions": 0, "changes": 1}
        for i in range(3)
    ]
    page_two = [
        {"filename": f"p2/file_{i}.py", "status": "added",
         "additions": 5, "deletions": 0, "changes": 5}
        for i in range(2)
    ]
    opener = _StubOpener(
        [
            (
                "/files",
                _json_body(page_one),
                {"Link": '<https://api.github.com/.../files?page=2>; rel="next"'},
            ),
            ("/files?page=2", _json_body(page_two), {}),
        ]
    )
    files = fetch_pr._fetch_files(
        owner="o", repo="r", pull_number=1, token="t", opener=opener
    )
    assert [f["filename"] for f in files] == [
        "p1/file_0.py", "p1/file_1.py", "p1/file_2.py",
        "p2/file_0.py", "p2/file_1.py",
    ]


def test_fetch_files_aborts_when_max_pages_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runaway ``Link`` chain must error rather than loop forever.

    Lower the page ceiling to 2 and feed three pages each pointing
    ``rel="next"`` at the next; the abort guard must fire on the
    third request before consuming page four.
    """

    monkeypatch.setattr(fetch_pr, "_FILES_MAX_PAGES", 2)

    queue: list[tuple[str, bytes, dict[str, str]]] = [
        (
            "api.github.com",
            _json_body([]),
            {"Link": '<https://api.github.com/x?page=2>; rel="next"'},
        ),
        (
            "api.github.com",
            _json_body([]),
            {"Link": '<https://api.github.com/x?page=3>; rel="next"'},
        ),
    ]
    opener = _StubOpener(queue)
    with pytest.raises(RuntimeError, match="more than 2 pages"):
        fetch_pr._fetch_files(
            owner="o", repo="r", pull_number=1, token="t", opener=opener
        )


def test_fetch_files_raises_on_non_list_response() -> None:
    """An object response (instead of an array) must surface immediately."""

    opener = _StubOpener([("/files", _json_body({"oops": True}), {})])
    with pytest.raises(RuntimeError, match="unexpected /pulls/.+/files response shape"):
        fetch_pr._fetch_files(
            owner="o", repo="r", pull_number=1, token="t", opener=opener
        )


def test_fetch_files_raises_on_non_object_file_entry() -> None:
    """A scalar entry inside the page array must surface immediately."""

    opener = _StubOpener([("/files", _json_body(["bare-string"]), {})])
    with pytest.raises(RuntimeError, match="unexpected file entry shape"):
        fetch_pr._fetch_files(
            owner="o", repo="r", pull_number=1, token="t", opener=opener
        )


# --- §10.1 mapping ---------------------------------------------------


_PULL_PAYLOAD: Final[dict[str, Any]] = {
    "title": "Add fuzzy search operator (closes #1)",
    "body": "Implements the `~` fuzzy operator. Closes #1.",
    "user": {"login": "octocat"},
    "base": {"ref": "main"},
    "head": {"ref": "feat/fuzzy"},
    "additions": 120,
    "deletions": 8,
    "changed_files": 3,
}


def test_map_pull_to_pr_record_pulls_every_field() -> None:
    pr = fetch_pr._map_pull_to_pr_record(_PULL_PAYLOAD)
    assert pr == {
        "title": "Add fuzzy search operator (closes #1)",
        "body": "Implements the `~` fuzzy operator. Closes #1.",
        "author": "octocat",
        "base_branch": "main",
        "head_branch": "feat/fuzzy",
        "additions": 120,
        "deletions": 8,
        "changed_files": 3,
    }


def test_map_pull_to_pr_record_handles_missing_optional_fields() -> None:
    """A draft / WIP PR can have a null body and missing user.login."""

    pr = fetch_pr._map_pull_to_pr_record(
        {
            "title": "wip",
            "body": None,
            "user": None,
            "base": {"ref": "main"},
            "head": {},
            "additions": None,
            "deletions": None,
            "changed_files": None,
        }
    )
    assert pr["body"] == ""
    assert pr["author"] == ""
    assert pr["head_branch"] == ""
    assert pr["additions"] == 0
    assert pr["deletions"] == 0
    assert pr["changed_files"] == 0


@pytest.mark.parametrize(
    ("github_status", "expected"),
    [
        ("added", "added"),
        ("modified", "modified"),
        ("removed", "removed"),
        ("renamed", "renamed"),
        ("copied", "renamed"),
        ("changed", "modified"),
        ("unchanged", "modified"),
    ],
)
def test_normalize_file_status_maps_github_to_engine_literal(
    github_status: str, expected: str
) -> None:
    """Every GitHub-documented status must land in the §10.1 closed set."""

    assert fetch_pr._normalize_file_status(github_status) == expected


def test_normalize_file_status_raises_on_unknown_status() -> None:
    """Unknown statuses surface immediately so a future API change is loud."""

    with pytest.raises(RuntimeError, match="unknown file status"):
        fetch_pr._normalize_file_status("brand_new")


def test_map_file_to_changed_file_round_trips_clean_entry() -> None:
    item = {
        "filename": "src/search/parser.py",
        "status": "modified",
        "additions": 10,
        "deletions": 2,
        "changes": 12,
        "patch": "@@ -1 +1 @@\n-x\n+y\n",
    }
    mapped = fetch_pr._map_file_to_changed_file(item)
    assert mapped == item


def test_map_file_to_changed_file_drops_non_string_patch() -> None:
    """A null/integer ``patch`` must be coerced to ``None``."""

    item = {
        "filename": "src/x.py",
        "status": "added",
        "additions": 1,
        "deletions": 0,
        "changes": 1,
        "patch": None,
    }
    mapped = fetch_pr._map_file_to_changed_file(item)
    assert mapped["patch"] is None


def test_map_file_to_changed_file_raises_on_missing_filename() -> None:
    with pytest.raises(RuntimeError, match="non-empty `filename`"):
        fetch_pr._map_file_to_changed_file(
            {"status": "added", "additions": 1, "deletions": 0, "changes": 1}
        )


def test_map_file_to_changed_file_raises_on_missing_status() -> None:
    with pytest.raises(RuntimeError, match="string `status`"):
        fetch_pr._map_file_to_changed_file(
            {"filename": "x.py", "additions": 1, "deletions": 0, "changes": 1}
        )


def test_build_engine_input_validates_via_pydantic() -> None:
    """A bad payload surfaces as a Pydantic ValidationError, not later."""

    from pydantic import ValidationError

    bogus_pull = dict(_PULL_PAYLOAD)
    bogus_pull["additions"] = -5
    with pytest.raises(ValidationError):
        fetch_pr.build_engine_input(bogus_pull, [])


def test_build_engine_input_returns_engine_input_for_valid_payload() -> None:
    files = [
        {
            "filename": "src/search/parser.py",
            "status": "modified",
            "additions": 10,
            "deletions": 2,
            "changes": 12,
        }
    ]
    engine_input = fetch_pr.build_engine_input(_PULL_PAYLOAD, files)
    assert isinstance(engine_input, EngineInput)
    assert engine_input.pr.title == _PULL_PAYLOAD["title"]
    assert len(engine_input.files) == 1
    assert engine_input.files[0].filename == "src/search/parser.py"


# --- main / CLI ------------------------------------------------------


def _setup_run_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    repo: str = "leo-aa88/reviewgate-core",
    pull_number: int = 1,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPOSITORY", repo)
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"number": pull_number}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))


def _patch_http(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[tuple[str, bytes, dict[str, str]]],
) -> _StubOpener:
    """Replace the module-level ``urlopen`` with the stub opener.

    The production code path uses ``urllib.request.urlopen`` directly
    when no opener is injected; tests patch that symbol so an
    end-to-end ``main()`` invocation stays hermetic.
    """

    opener = _StubOpener(responses)

    def fake_urlopen(
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> _StubResponse:
        return opener.open(request, timeout=timeout)

    monkeypatch.setattr(fetch_pr.urllib.request, "urlopen", fake_urlopen)
    return opener


def test_main_writes_engine_input_to_output_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End-to-end: env contract -> two stubbed HTTP calls -> §10.1 JSON."""

    _setup_run_env(monkeypatch, tmp_path)
    _patch_http(
        monkeypatch,
        [
            ("/pulls/1", _json_body(_PULL_PAYLOAD), {}),
            (
                "/pulls/1/files",
                _json_body(
                    [
                        {
                            "filename": "src/search/parser.py",
                            "status": "modified",
                            "additions": 10,
                            "deletions": 2,
                            "changes": 12,
                            "patch": "@@ -1 +1 @@\n-x\n+y\n",
                        }
                    ]
                ),
                {},
            ),
        ],
    )

    output = tmp_path / "engine_input.json"
    exit_code = fetch_pr.main(["--output", str(output)])

    assert exit_code == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    EngineInput.model_validate(written)
    assert written["pr"]["title"] == _PULL_PAYLOAD["title"]
    assert written["files"][0]["filename"] == "src/search/parser.py"


def test_main_writes_engine_input_to_stdout_when_no_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--output`` is optional; absence sends the JSON to stdout."""

    _setup_run_env(monkeypatch, tmp_path)
    _patch_http(
        monkeypatch,
        [
            ("/pulls/1", _json_body(_PULL_PAYLOAD), {}),
            ("/pulls/1/files", _json_body([]), {}),
        ],
    )

    exit_code = fetch_pr.main([])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    EngineInput.model_validate(json.loads(captured.out))


def test_main_returns_two_when_env_var_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing env var must exit 2 with the var name on stderr."""

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    exit_code = fetch_pr.main([])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "GITHUB_TOKEN" in captured.err


def test_main_returns_two_when_pull_number_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A push-shaped event payload must exit 2, not crash."""

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"action": "push"}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))

    exit_code = fetch_pr.main([])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "pull request number" in captured.err
