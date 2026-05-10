"""§13 PR-comment upsert against the GitHub Issues API (issue #26).

A single function -- :func:`upsert_comment` -- drives the comment
flow:

1. Compose the comment body from the §10.2 ``ReviewabilityReport``
   plus the §13 marker (an HTML comment) so the upsert can detect
   the previous ReviewGate comment by content rather than by
   storing a comment id elsewhere. The marker stays embedded even
   after edits, which means the Action can re-find it after a
   force-push or a re-run that lost any in-memory state.
2. List the PR's issue comments via the paginated REST endpoint
   (``GET /repos/{owner}/{repo}/issues/{n}/comments``) and find
   the first one whose body contains the marker. Pagination
   follows the `Link: rel="next"` header just like the file
   listing in :mod:`reviewgate_action.fetch_pr`; the loop is
   capped at :data:`_COMMENTS_MAX_PAGES` (30 pages = 3000
   comments, way past anything realistic) so a runaway thread
   cannot loop the Action against the API.
3. ``PATCH`` the existing comment when found, otherwise ``POST``
   a new one.

Pure stdlib, single-shot HTTP, no Pydantic for HTTP responses --
this module mirrors the layering decisions documented in
:mod:`reviewgate_action.fetch_pr` and matches the existing
``scripts/_pr_review_http.py`` style so a maintainer who knows one
HTTP layer knows both.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Final

from reviewgate.core.schemas import ReviewabilityReport

_GITHUB_API_BASE: Final[str] = "https://api.github.com"
_GITHUB_API_VERSION: Final[str] = "2022-11-28"
_USER_AGENT: Final[str] = "reviewgate-action/issues-comments"
_TIMEOUT_SECONDS: Final[float] = 30.0

# Stable §13 marker. The version suffix lets us evolve the body
# format later (e.g. add a structured JSON block) without losing the
# upsert path on legacy comments: the regex ignores the version so a
# v1 marker still matches a v2 comment for the upsert lookup.
_MARKER_REGEX: Final[re.Pattern[str]] = re.compile(
    r"<!--\s*reviewgate:marker(?::v\d+)?\s*-->"
)
MARKER: Final[str] = "<!-- reviewgate:marker:v1 -->"

_COMMENTS_MAX_PAGES: Final[int] = 30
"""Pagination ceiling for the comments listing (matches fetch_pr)."""

_LINK_NEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"<([^>]+)>;\s*rel=\"next\"", re.IGNORECASE
)


# --- HTTP helpers ----------------------------------------------------


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": _USER_AGENT,
    }


def _http_request(
    method: str,
    url: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    opener: Any | None = None,
) -> tuple[Any, dict[str, str]]:
    """Issue ``method`` against ``url`` and return ``(json, headers)``.

    Mirrors :func:`reviewgate_action.fetch_pr._http_get_json` but with
    method + body support so the same error-translation logic covers
    POST and PATCH. ``opener`` is the dependency-injection seam the
    test suite uses to avoid real network traffic.
    """

    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url=url, method=method, data=data)
    for header, value in _github_headers(token).items():
        request.add_header(header, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")

    open_fn = opener.open if opener is not None else urllib.request.urlopen
    try:
        with open_fn(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read()
            headers = {
                key.lower(): value for key, value in response.headers.items()
            }
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"GitHub API {method} {url} returned HTTP {exc.code} "
            f"{exc.reason}"
        ) from exc
    if not raw:
        return None, headers
    try:
        return json.loads(raw.decode("utf-8")), headers
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"GitHub API {method} {url} returned a body that is not valid "
            f"JSON: {exc}"
        ) from exc


def _next_url(headers: dict[str, str]) -> str | None:
    link = headers.get("link")
    if not link:
        return None
    match = _LINK_NEXT_RE.search(link)
    return match.group(1) if match else None


# --- comment lookup + upsert -----------------------------------------


def _comments_url(owner: str, repo: str, pull_number: int) -> str:
    return (
        f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues/"
        f"{pull_number}/comments?per_page=100"
    )


def find_existing_comment_id(
    *,
    owner: str,
    repo: str,
    pull_number: int,
    token: str,
    opener: Any | None = None,
) -> int | None:
    """Return the id of the §13 ReviewGate comment, or ``None``.

    Iterates over the PR's issue comments (paginated, capped at
    :data:`_COMMENTS_MAX_PAGES`) and matches each body against
    :data:`_MARKER_REGEX`. The first match wins; if the bot ever
    posted twice (e.g. due to a race), only the older comment is
    upserted and the newer one is left in place so a human can see
    the duplicate and clean it up.
    """

    url: str | None = _comments_url(owner, repo, pull_number)
    pages_seen = 0
    while url is not None:
        pages_seen += 1
        if pages_seen > _COMMENTS_MAX_PAGES:
            raise RuntimeError(
                "GitHub returned more than "
                f"{_COMMENTS_MAX_PAGES} pages of issue comments for "
                f"PR #{pull_number}; refusing to keep paginating"
            )
        body, headers = _http_request("GET", url, token=token, opener=opener)
        if not isinstance(body, list):
            raise RuntimeError(
                f"unexpected /issues/{pull_number}/comments response shape "
                f"(expected list, got {type(body).__name__})"
            )
        for entry in body:
            if not isinstance(entry, dict):
                continue
            comment_body = entry.get("body")
            if isinstance(comment_body, str) and _MARKER_REGEX.search(comment_body):
                comment_id = entry.get("id")
                if isinstance(comment_id, int):
                    return comment_id
        url = _next_url(headers)
    return None


def render_comment_body(report: ReviewabilityReport, summary_md: str) -> str:
    """Wrap the human Markdown summary with the stable §13 marker.

    The marker sits on its own line at the top so the upsert lookup
    succeeds even when the summary grows or is reformatted in a
    future change. The verdict is repeated in the heading so a
    user collapsing the marker line still sees the result.
    """

    body = summary_md.rstrip()
    return f"{MARKER}\n\n{body}\n"


def upsert_comment(
    *,
    owner: str,
    repo: str,
    pull_number: int,
    token: str,
    report: ReviewabilityReport,
    summary_md: str,
    opener: Any | None = None,
) -> tuple[str, int]:
    """Upsert the §13 PR comment.

    Returns a ``(action, comment_id)`` tuple where ``action`` is
    either ``"created"`` or ``"updated"`` so the caller can log the
    outcome. ``comment_id`` is GitHub's numeric id for the comment.
    """

    body_text = render_comment_body(report, summary_md)
    payload = {"body": body_text}

    existing = find_existing_comment_id(
        owner=owner,
        repo=repo,
        pull_number=pull_number,
        token=token,
        opener=opener,
    )

    if existing is not None:
        url = (
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues/comments/"
            f"{existing}"
        )
        response, _headers = _http_request(
            "PATCH", url, token=token, body=payload, opener=opener
        )
        comment_id = (
            response["id"]
            if isinstance(response, dict) and isinstance(response.get("id"), int)
            else existing
        )
        return "updated", comment_id

    url = _comments_url(owner, repo, pull_number).split("?", 1)[0]
    response, _headers = _http_request(
        "POST", url, token=token, body=payload, opener=opener
    )
    if not isinstance(response, dict) or not isinstance(response.get("id"), int):
        raise RuntimeError(
            "GitHub returned an unexpected response shape from POST "
            f"{url}; expected an object with an `id` field"
        )
    return "created", response["id"]


# --- run_core glue ---------------------------------------------------
#
# Lives here (rather than inside ``run_core``) so the comment-flow
# orchestration -- env-var resolution + the non-fatal failure policy
# -- stays next to the upsert it wraps. ``run_core`` is the CLI entry
# point and stays focused on input parsing + the engine call.


def _resolve_repo_and_pull(
    repo_arg: str | None,
    pull_arg: int | None,
) -> tuple[str, str, int]:
    """Resolve owner/repo + PR number from CLI flags or env vars.

    The Action sets ``GITHUB_REPOSITORY`` and ``GITHUB_EVENT_PATH``
    automatically; local invocations either pass ``--repo`` and
    ``--pull-number`` or set the same env vars to keep the test
    plumbing identical.
    """

    from reviewgate_action.fetch_pr import (  # local import: avoid cycle
        _pull_number_from_event,
        _split_repo,
    )

    repo_value = repo_arg or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo_value:
        raise RuntimeError(
            "missing repository (pass --repo or set GITHUB_REPOSITORY)"
        )
    owner, repo = _split_repo(repo_value)

    if pull_arg is not None:
        return owner, repo, pull_arg

    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        raise RuntimeError(
            "missing pull request number (pass --pull-number or set "
            "GITHUB_EVENT_PATH)"
        )
    return owner, repo, _pull_number_from_event(event_path)


def upsert_from_environment(
    *,
    report: ReviewabilityReport,
    summary_md: str,
    repo_arg: str | None,
    pull_arg: int | None,
    log_prefix: str,
) -> None:
    """Run the §13 upsert with env-var fallbacks; never raises.

    Failures here (missing token, GitHub API errors, malformed event
    payloads) are non-fatal: the engine result already drove the
    workflow exit code via ``fail-on``. This helper logs each
    failure mode on stderr so an operator can debug the missing
    permission or wrong token, but always returns ``None`` so the
    caller can keep its exit-code logic simple.
    """

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.stderr.write(
            f"{log_prefix}: GITHUB_TOKEN not set; skipping comment upsert\n"
        )
        return

    try:
        owner, repo, pull_number = _resolve_repo_and_pull(repo_arg, pull_arg)
    except RuntimeError as exc:
        sys.stderr.write(f"{log_prefix}: cannot post comment: {exc}\n")
        return

    try:
        action, comment_id = upsert_comment(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            token=token,
            report=report,
            summary_md=summary_md,
        )
    except RuntimeError as exc:
        sys.stderr.write(f"{log_prefix}: comment upsert failed: {exc}\n")
        return

    sys.stderr.write(
        f"{log_prefix}: {action} ReviewGate comment id={comment_id} on "
        f"{owner}/{repo}#{pull_number}\n"
    )


__all__ = [
    "MARKER",
    "find_existing_comment_id",
    "render_comment_body",
    "upsert_comment",
    "upsert_from_environment",
]
