"""Fetch a PR's metadata + files from GitHub and emit §10.1 EngineInput.

Implements issue #24 of Milestone 3. This module is the open-source
GitHub Action's I/O boundary: it reads the GitHub REST API and writes
a JSON document matching :class:`reviewgate.core.schemas.EngineInput`
to disk (or stdout) so the next step in
``reviewgate-action/action.yml`` can pipe it straight into the pure
deterministic engine.

Design choices:

* **Stdlib only.** Uses :mod:`urllib.request` for HTTP; no ``httpx``
  / ``requests`` dependency. Keeps Action cold-start fast and
  matches the same pattern used by ``scripts/_pr_review_http.py``
  in this repo.
* **Reads the workflow event payload.** GitHub Actions writes the
  triggering ``pull_request`` event JSON to ``$GITHUB_EVENT_PATH``;
  this module parses that file to get the PR number rather than
  taking it as a CLI argument. That keeps the Action one-step
  configurable and avoids a second source of truth.
* **Pagination.** The GitHub Files API caps each page at 100; this
  module follows the documented ``Link: rel="next"`` header so the
  full file list is materialized even on PRs that touch hundreds
  of files.
* **§10.1 contract.** The output is round-tripped through
  :class:`reviewgate.core.schemas.EngineInput` before being
  serialized, so the JSON the next step consumes is guaranteed to
  validate; a schema drift between this fetcher and the engine
  fails fast at fetch time instead of leaking into ``analyze()``.

CLI surface:

::

    python -m reviewgate_action.fetch_pr --output engine_input.json

Without ``--output`` the JSON goes to stdout (handy for ad-hoc
debugging in workflow logs). The exit code is 0 on success and 2
on every documented failure mode (missing env var, malformed event
payload, GitHub API error); 2 is the conventional "input/usage
error" exit code shared with the rest of the open-source CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from reviewgate.core.schemas import EngineInput

_PROG: Final[str] = "reviewgate-action.fetch_pr"

_EXIT_OK: Final[int] = 0
_EXIT_ERROR: Final[int] = 2

# GitHub REST API constants. The version pin matches what every
# `scripts/*` helper in this repo sends, so a change here is a
# repo-wide decision rather than a single-Action drift.
_GITHUB_API: Final[str] = "https://api.github.com"
_GITHUB_API_VERSION: Final[str] = "2022-11-28"
_USER_AGENT: Final[str] = "reviewgate-action-fetch/1"
_HTTP_TIMEOUT_SECS: Final[float] = 30.0
_FILES_PAGE_SIZE: Final[int] = 100
_FILES_MAX_PAGES: Final[int] = 30
"""Hard ceiling on file-list pages (``30 * 100 = 3000`` files).

Matches the §10 design assumption that a PR touching more than a few
thousand files is already in fail-tier territory; a hostile or runaway
PR cannot make the Action loop indefinitely against the API."""

_LINK_NEXT_RE: Final[re.Pattern[str]] = re.compile(
    r'<([^>]+)>;\s*rel="next"',
)
_REPO_RE: Final[re.Pattern[str]] = re.compile(r"^[\w.-]+/[\w.-]+$")


# --- env-var contract ------------------------------------------------


def _required_env(name: str) -> str:
    """Return ``os.environ[name]`` or raise a clear runtime error.

    Pulled out so each missing-variable error path produces the same
    operator-facing message ("environment variable X is required"),
    instead of bubbling up a bare :class:`KeyError`.
    """

    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"environment variable {name} is required when running the "
            f"reviewgate-action fetch step (set automatically by GitHub "
            "Actions for `pull_request` events)"
        )
    return value


def _split_repo(slug: str) -> tuple[str, str]:
    """Split ``owner/repo`` into ``(owner, repo)`` with strict validation.

    GitHub's ``$GITHUB_REPOSITORY`` is documented as ``owner/repo`` with
    no nested path segments; rejecting other shapes early prevents a
    malformed value from silently rendering an invalid REST URL.
    """

    if not _REPO_RE.match(slug):
        raise RuntimeError(
            f"GITHUB_REPOSITORY must be in the form 'owner/repo' "
            f"(got: {slug!r})"
        )
    owner, repo = slug.split("/", 1)
    return owner, repo


def _pull_number_from_event(event_path: str) -> int:
    """Extract the PR number from a GitHub Actions ``pull_request`` event.

    The webhook payload has ``pull_request.number`` (preferred) or
    ``number`` at the top level depending on event subtype. We accept
    either so the same fetcher works for ``pull_request_target`` and
    other PR-shaped events.
    """

    try:
        raw = Path(event_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"could not read GITHUB_EVENT_PATH at {event_path!r}: {exc}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"GITHUB_EVENT_PATH at {event_path!r} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"GITHUB_EVENT_PATH at {event_path!r} must be a JSON object"
        )
    pr_block = payload.get("pull_request")
    if isinstance(pr_block, dict):
        candidate = pr_block.get("number")
        if isinstance(candidate, int):
            return candidate
    top_number = payload.get("number")
    if isinstance(top_number, int):
        return top_number
    raise RuntimeError(
        "could not find a pull request number in GITHUB_EVENT_PATH; "
        "this fetcher only supports pull_request events"
    )


# --- HTTP layer (stdlib) ---------------------------------------------


def _github_headers(token: str) -> dict[str, str]:
    """Return the canonical request headers for a GitHub REST call."""

    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": _USER_AGENT,
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }


def _http_get_json(
    url: str,
    *,
    token: str,
    opener: urllib.request.OpenerDirector | None = None,
) -> tuple[Any, dict[str, str]]:
    """GET ``url`` with the Action's headers and return ``(json, headers)``.

    ``opener`` is injected by tests so the HTTP layer can be replaced
    with a deterministic stub; production callers leave it ``None``
    and the module uses :func:`urllib.request.urlopen` directly.

    Raises:
        RuntimeError: any non-2xx response, network error, or response
            body that is not valid JSON. The message includes the
            request URL so operators can correlate failures with API
            paths in the workflow log.
    """

    request = urllib.request.Request(  # noqa: S310 - controlled URL
        url,
        headers=_github_headers(token),
        method="GET",
    )
    try:
        if opener is None:
            response = urllib.request.urlopen(  # noqa: S310 - controlled URL
                request, timeout=_HTTP_TIMEOUT_SECS
            )
        else:
            response = opener.open(request, timeout=_HTTP_TIMEOUT_SECS)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API request failed for {url}: HTTP {exc.code} {exc.reason}\n"
            f"{body[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"GitHub API request failed for {url}: {exc.reason}"
        ) from exc

    with response:
        raw = response.read().decode("utf-8")
        headers = {key.lower(): value for key, value in response.headers.items()}
    try:
        return json.loads(raw), headers
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"GitHub API response from {url} was not valid JSON: {exc}"
        ) from exc


def _next_url(headers: dict[str, str]) -> str | None:
    """Extract the ``rel="next"`` URL from the API ``Link`` header.

    Returns ``None`` when the header is absent or carries no ``next``
    relation, signalling that pagination is complete.
    """

    link = headers.get("link")
    if not link:
        return None
    match = _LINK_NEXT_RE.search(link)
    if match is None:
        return None
    return match.group(1)


# --- GitHub REST calls -----------------------------------------------


def _fetch_pull(
    *,
    owner: str,
    repo: str,
    pull_number: int,
    token: str,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, Any]:
    """``GET /repos/{owner}/{repo}/pulls/{pull_number}``.

    Returns the parsed JSON object as a plain dict; the caller maps it
    onto the §10.1 :class:`reviewgate.core.schemas.PRRecord` shape.
    """

    url = f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pull_number}"
    payload, _headers = _http_get_json(url, token=token, opener=opener)
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"unexpected /pulls/{pull_number} response shape: "
            f"{type(payload).__name__}"
        )
    return payload


def _fetch_files(
    *,
    owner: str,
    repo: str,
    pull_number: int,
    token: str,
    opener: urllib.request.OpenerDirector | None = None,
) -> list[dict[str, Any]]:
    """``GET /repos/{owner}/{repo}/pulls/{pull_number}/files`` (paginated).

    Returns a flat list across every page. Stops at
    :data:`_FILES_MAX_PAGES` to bound runtime even if the API
    repeatedly hands back ``rel="next"`` (e.g. on a hostile PR with
    runaway file count).
    """

    files: list[dict[str, Any]] = []
    base = f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pull_number}/files"
    url: str | None = f"{base}?per_page={_FILES_PAGE_SIZE}"
    pages_seen = 0
    while url is not None:
        if pages_seen >= _FILES_MAX_PAGES:
            raise RuntimeError(
                f"GitHub /pulls/{pull_number}/files returned more than "
                f"{_FILES_MAX_PAGES} pages; aborting to bound runtime"
            )
        chunk, headers = _http_get_json(url, token=token, opener=opener)
        if not isinstance(chunk, list):
            raise RuntimeError(
                f"unexpected /pulls/{pull_number}/files response shape on "
                f"page {pages_seen + 1}: {type(chunk).__name__}"
            )
        for item in chunk:
            if not isinstance(item, dict):
                raise RuntimeError(
                    "unexpected file entry shape in /pulls/.../files: "
                    f"{type(item).__name__}"
                )
            files.append(item)
        pages_seen += 1
        url = _next_url(headers)
    return files


# --- §10.1 mapping ---------------------------------------------------

_ALLOWED_FILE_STATUS: Final[frozenset[str]] = frozenset(
    {"added", "modified", "removed", "renamed"},
)


def _normalize_file_status(status: str) -> str:
    """Map GitHub's wider ``status`` field onto the §10.1 closed set.

    GitHub additionally returns ``copied``, ``changed``, and
    ``unchanged``; the deterministic engine's :class:`FileStatus`
    Literal only declares the four spec values, so we coerce
    ``copied -> renamed`` (semantically equivalent for review) and
    ``changed/unchanged -> modified`` (best-effort fallback). An
    entirely unknown status raises so a future GitHub API change
    surfaces immediately rather than as a Pydantic error downstream.
    """

    if status in _ALLOWED_FILE_STATUS:
        return status
    if status == "copied":
        return "renamed"
    if status in ("changed", "unchanged"):
        return "modified"
    raise RuntimeError(
        f"GitHub returned an unknown file status {status!r}; the §10.1 "
        "schema only allows added | modified | removed | renamed"
    )


def _map_pull_to_pr_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Project the /pulls payload onto the §10.1 ``pr`` block."""

    user = payload.get("user")
    author = ""
    if isinstance(user, dict):
        login = user.get("login")
        if isinstance(login, str):
            author = login
    base = payload.get("base")
    head = payload.get("head")
    base_branch = base.get("ref") if isinstance(base, dict) else ""
    head_branch = head.get("ref") if isinstance(head, dict) else ""
    return {
        "title": payload.get("title") or "",
        "body": payload.get("body") or "",
        "author": author,
        "base_branch": base_branch or "",
        "head_branch": head_branch or "",
        "additions": int(payload.get("additions") or 0),
        "deletions": int(payload.get("deletions") or 0),
        "changed_files": int(payload.get("changed_files") or 0),
    }


def _map_file_to_changed_file(item: dict[str, Any]) -> dict[str, Any]:
    """Project one /files entry onto a §10.1 ``files[*]`` row."""

    status_raw = item.get("status")
    if not isinstance(status_raw, str):
        raise RuntimeError(
            f"file entry is missing a string `status`: {item!r}"
        )
    filename = item.get("filename")
    if not isinstance(filename, str) or not filename:
        raise RuntimeError(
            f"file entry is missing a non-empty `filename`: {item!r}"
        )
    patch = item.get("patch")
    return {
        "filename": filename,
        "status": _normalize_file_status(status_raw),
        "additions": int(item.get("additions") or 0),
        "deletions": int(item.get("deletions") or 0),
        "changes": int(item.get("changes") or 0),
        "patch": patch if isinstance(patch, str) else None,
    }


def build_engine_input(
    pull_payload: dict[str, Any],
    file_payloads: Iterable[dict[str, Any]],
) -> EngineInput:
    """Assemble a validated :class:`EngineInput` from raw GitHub payloads.

    Centralised so both the CLI ``main`` and the unit tests share the
    same construction path. Validation runs through Pydantic so a
    contract drift between this fetcher and §10.1 fails here, with a
    structured error pointing at the offending field, instead of much
    later in :func:`reviewgate.core.engine.analyze`.
    """

    pr_block = _map_pull_to_pr_record(pull_payload)
    files_block = [_map_file_to_changed_file(f) for f in file_payloads]
    return EngineInput.model_validate(
        {
            "pr": pr_block,
            "files": files_block,
            "config": {},
        }
    )


# --- CLI -------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=(
            "Fetch the triggering pull request's metadata and changed "
            "files from the GitHub REST API and emit a §10.1 EngineInput "
            "JSON document for `reviewgate.core.engine.analyze`."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help=(
            "Path to write the §10.1 JSON document to. When omitted the "
            "JSON is written to stdout."
        ),
    )
    return parser


def _resolve_run_inputs() -> tuple[str, str, str, int]:
    """Return ``(token, owner, repo, pull_number)`` from the env contract.

    Centralized so the CLI ``main`` and the unit tests follow the
    same env-var lookup; tests can monkeypatch ``os.environ`` to
    drive the behaviour without re-implementing the parsing.
    """

    token = _required_env("GITHUB_TOKEN")
    repo_slug = _required_env("GITHUB_REPOSITORY")
    event_path = _required_env("GITHUB_EVENT_PATH")
    owner, repo = _split_repo(repo_slug)
    pull_number = _pull_number_from_event(event_path)
    return token, owner, repo, pull_number


def main(argv: list[str] | None = None) -> int:
    """Console entry point for ``python -m reviewgate_action.fetch_pr``.

    Returns 0 on success and 2 on any documented failure mode. The
    exit code matches the open-source ``reviewgate-core`` CLI's
    "input/usage error" code so the wrapper Action can branch on a
    single non-zero signal.
    """

    args = _build_parser().parse_args(argv)
    try:
        token, owner, repo, pull_number = _resolve_run_inputs()
        pull_payload = _fetch_pull(
            owner=owner, repo=repo, pull_number=pull_number, token=token
        )
        file_payloads = _fetch_files(
            owner=owner, repo=repo, pull_number=pull_number, token=token
        )
        engine_input = build_engine_input(pull_payload, file_payloads)
    except RuntimeError as exc:
        print(f"{_PROG}: {exc}", file=sys.stderr)
        return _EXIT_ERROR

    serialized = engine_input.model_dump_json(indent=2)
    if args.output is None:
        sys.stdout.write(serialized)
        sys.stdout.write("\n")
    else:
        Path(args.output).write_text(serialized + "\n", encoding="utf-8")
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via the module
    raise SystemExit(main())


__all__ = [
    "build_engine_input",
    "main",
]
