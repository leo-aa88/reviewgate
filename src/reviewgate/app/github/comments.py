"""GitHub issue-comment upsert for ReviewGate PR reports (``docs/DESIGN.md`` §13.8, §18; issue #51).

Pull-request threads use the **issue comments** API. ReviewGate keeps a single
persistent comment: search for the hidden HTML marker and a matching App bot
author, then ``PATCH`` that row; otherwise ``POST`` a new comment. Comments that
contain the marker but were written by a human or another bot are never
updated.

Example:
    Upsert after resolving the installation bot login::

        from pydantic import SecretStr

        from reviewgate.app.github.comments import (
            resolve_reviewgate_bot_login,
            upsert_reviewgate_report_issue_comment,
        )
        from reviewgate.app.settings import AppSettings

        settings = AppSettings(github_app_bot_login="my-app[bot]")
        login = resolve_reviewgate_bot_login(settings)
        upsert_reviewgate_report_issue_comment(
            SecretStr("ghs_…"),
            owner="acme",
            repo="demo",
            issue_number=42,
            body_markdown="## ReviewGate: PASS\\n…",
            bot_login=login,
        )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from reviewgate.app.github.client import (
    GitHubRestError,
    _installation_auth_headers,
    _raise_for_github_response,
    _validate_repo_segment,
)
from reviewgate.app.settings import AppSettings

logger = logging.getLogger(__name__)

_GITHUB_API_ORIGIN: Final[str] = "https://api.github.com"
_DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0
_ISSUE_COMMENTS_PER_PAGE: Final[int] = 100

#: Hidden marker for idempotent comment updates (``docs/DESIGN.md`` §13.8).
REVIEWGATE_REPORT_MARKER: Final[str] = "<!-- reviewgate-report -->"


@dataclass(frozen=True, slots=True)
class UpsertCommentResult:
    """Outcome of :func:`upsert_reviewgate_report_issue_comment`.

    Attributes:
        comment_id: GitHub ``issues/comments/{id}`` identifier.
        updated: ``True`` when an existing comment was patched; ``False`` when
            created.
    """

    comment_id: int
    updated: bool


def resolve_reviewgate_bot_login(settings: AppSettings) -> str:
    """Return the GitHub login used to author ReviewGate PR comments.

    Prefer an explicit operator override; otherwise derive ``{slug}[bot]`` from
    the App slug (``docs/DESIGN.md`` §13.8).

    Args:
        settings: Process settings (``REVIEWGATE_GITHUB_APP_BOT_LOGIN`` or
            ``REVIEWGATE_GITHUB_APP_SLUG``).

    Returns:
        Non-empty login string (for example ``\"reviewgate-local[bot]\"``).

    Raises:
        ValueError: When neither ``github_app_bot_login`` nor ``github_app_slug``
            is configured with a non-empty value.
    """

    if settings.github_app_bot_login:
        login = settings.github_app_bot_login.strip()
        if login:
            return login
    if settings.github_app_slug:
        slug = settings.github_app_slug.strip()
        if slug:
            return f"{slug}[bot]"
    msg = (
        "Set REVIEWGATE_GITHUB_APP_BOT_LOGIN to the App bot login "
        "(e.g. my-app[bot]) or REVIEWGATE_GITHUB_APP_SLUG for {slug}[bot]"
    )
    raise ValueError(msg)


def format_reviewgate_report_body(markdown: str) -> str:
    """Ensure the report body includes the §13.8 marker once at the top.

    If ``markdown`` already begins with :data:`REVIEWGATE_REPORT_MARKER` (after
    leading whitespace), it is returned unchanged aside from stripping leading
    whitespace-only prefix. Otherwise the marker is prepended with a blank line
    before the caller body.

    Args:
        markdown: Markdown payload for the PR comment (§18 tone).

    Returns:
        Full comment body suitable for create/update API calls.
    """

    stripped = markdown.lstrip()
    if stripped.startswith(REVIEWGATE_REPORT_MARKER):
        return stripped
    return f"{REVIEWGATE_REPORT_MARKER}\n\n{stripped}"


def find_reviewgate_report_comment_for_upsert(
    comments: list[dict[str, Any]],
    *,
    bot_login: str,
) -> dict[str, Any] | None:
    """Pick the last issue comment authored by ``bot_login`` that carries the marker.

    GitHub returns comments in ascending creation order. The last match is the
    most recent ReviewGate comment and is the one to update.

    Args:
        comments: Parsed JSON objects from the issue-comments list endpoint.
        bot_login: Expected bot ``user.login`` (case-sensitive per GitHub).

    Returns:
        The matching comment document, or ``None`` when no safe update target
        exists (including when only humans or other bots used the marker).
    """

    expected = bot_login.strip()
    if not expected:
        return None
    candidate: dict[str, Any] | None = None
    for raw in comments:
        if not isinstance(raw, dict):
            continue
        user_obj = raw.get("user")
        if not isinstance(user_obj, dict):
            continue
        login = user_obj.get("login")
        if not isinstance(login, str) or login != expected:
            continue
        body = raw.get("body")
        if not isinstance(body, str):
            continue
        if REVIEWGATE_REPORT_MARKER not in body:
            continue
        candidate = raw
    return candidate


def list_issue_comments(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    issue_number: int,
    http_client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """List all issue comments for a pull request (issue) with pagination.

    Args:
        installation_token: Installation access token (Bearer).
        owner: Repository owner login.
        repo: Repository name.
        issue_number: Pull request number (same as issue number on GitHub).
        http_client: Optional shared HTTP client.

    Returns:
        Comment objects as returned by GitHub (dicts with ``id``, ``user``,
        ``body``, etc.).

    Raises:
        ValueError: When ``issue_number`` is not positive.
        GitHubRestError: On HTTP failure after classification.
    """

    if issue_number < 1:
        msg = "issue_number must be a positive integer"
        raise ValueError(msg)
    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    base_url = (
        f"{_GITHUB_API_ORIGIN}/repos/"
        f"{quote(own, safe='')}/{quote(rep, safe='')}/issues/{issue_number}/comments"
    )
    headers = _installation_auth_headers(installation_token)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    aggregated: list[dict[str, Any]] = []
    page = 1
    try:
        while True:
            try:
                response = client.get(
                    base_url,
                    headers=headers,
                    params={"per_page": _ISSUE_COMMENTS_PER_PAGE, "page": page},
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "github_issue_comments_transport_error",
                    extra={"github_detail": str(exc), "page": page},
                )
                msg = "HTTP transport error while listing issue comments"
                raise GitHubRestError(
                    msg,
                    status_code=None,
                    retriable=True,
                    request_id=None,
                ) from exc
            _raise_for_github_response(
                operation="list_issue_comments",
                response=response,
            )
            try:
                chunk: object = response.json()
            except ValueError as exc:
                msg = "GitHub issue comments response was not valid JSON"
                raise GitHubRestError(
                    msg,
                    status_code=response.status_code,
                    retriable=False,
                    request_id=response.headers.get("x-github-request-id"),
                ) from exc
            if not isinstance(chunk, list):
                msg = "GitHub issue comments response JSON was not an array"
                raise GitHubRestError(
                    msg,
                    status_code=response.status_code,
                    retriable=False,
                    request_id=response.headers.get("x-github-request-id"),
                )
            for item in chunk:
                if not isinstance(item, dict):
                    msg = "GitHub issue comments response contained non-object entries"
                    raise GitHubRestError(
                        msg,
                        status_code=response.status_code,
                        retriable=False,
                        request_id=response.headers.get("x-github-request-id"),
                    )
                aggregated.append(item)
            if len(chunk) < _ISSUE_COMMENTS_PER_PAGE:
                break
            page += 1
    finally:
        if owns_client:
            client.close()

    return aggregated


def upsert_reviewgate_report_issue_comment(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    issue_number: int,
    body_markdown: str,
    bot_login: str,
    http_client: httpx.Client | None = None,
) -> UpsertCommentResult:
    """Create or update the persistent ReviewGate PR comment (§13.8).

    Selects an existing comment only when **both** the configured bot login and
    :data:`REVIEWGATE_REPORT_MARKER` match; otherwise posts a new comment.

    Args:
        installation_token: Installation access token with ``issues:write``.
        owner: Repository owner login.
        repo: Repository name.
        issue_number: Pull request number.
        body_markdown: Markdown body (marker added via
            :func:`format_reviewgate_report_body` unless already present).
        bot_login: Expected ``user.login`` for ReviewGate (from
            :func:`resolve_reviewgate_bot_login`).
        http_client: Optional shared HTTP client.

    Returns:
        Identifiers and whether the comment was patched vs created.

    Raises:
        ValueError: When ``bot_login`` is empty or ``issue_number`` is invalid.
        GitHubRestError: On HTTP failure.
    """

    if issue_number < 1:
        msg = "issue_number must be a positive integer"
        raise ValueError(msg)
    trimmed_login = bot_login.strip()
    if not trimmed_login:
        msg = "bot_login must be a non-empty GitHub login"
        raise ValueError(msg)

    body = format_reviewgate_report_body(body_markdown)
    comments = list_issue_comments(
        installation_token,
        owner=owner,
        repo=repo,
        issue_number=issue_number,
        http_client=http_client,
    )
    existing = find_reviewgate_report_comment_for_upsert(
        comments,
        bot_login=trimmed_login,
    )

    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    headers = _installation_auth_headers(installation_token)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        if existing is not None:
            cid = existing.get("id")
            if not isinstance(cid, int):
                msg = "GitHub comment payload missing integer id"
                raise GitHubRestError(
                    msg,
                    status_code=None,
                    retriable=False,
                    request_id=None,
                )
            url = (
                f"{_GITHUB_API_ORIGIN}/repos/"
                f"{quote(own, safe='')}/{quote(rep, safe='')}/issues/comments/{cid}"
            )
            try:
                response = client.patch(url, headers=headers, json={"body": body})
            except httpx.HTTPError as exc:
                logger.warning(
                    "github_patch_comment_transport_error",
                    extra={"github_detail": str(exc), "comment_id": cid},
                )
                msg = "HTTP transport error while updating issue comment"
                raise GitHubRestError(
                    msg,
                    status_code=None,
                    retriable=True,
                    request_id=None,
                ) from exc
            _raise_for_github_response(
                operation="update_issue_comment",
                response=response,
            )
            return UpsertCommentResult(comment_id=cid, updated=True)

        create_url = (
            f"{_GITHUB_API_ORIGIN}/repos/"
            f"{quote(own, safe='')}/{quote(rep, safe='')}/issues/{issue_number}/comments"
        )
        try:
            response = client.post(create_url, headers=headers, json={"body": body})
        except httpx.HTTPError as exc:
            logger.warning(
                "github_create_comment_transport_error",
                extra={"github_detail": str(exc)},
            )
            msg = "HTTP transport error while creating issue comment"
            raise GitHubRestError(
                msg,
                status_code=None,
                retriable=True,
                request_id=None,
            ) from exc
        _raise_for_github_response(
            operation="create_issue_comment",
            response=response,
        )
        try:
            created: object = response.json()
        except ValueError as exc:
            msg = "GitHub create comment response was not valid JSON"
            raise GitHubRestError(
                msg,
                status_code=response.status_code,
                retriable=False,
                request_id=response.headers.get("x-github-request-id"),
            ) from exc
        if not isinstance(created, dict):
            msg = "GitHub create comment response JSON was not an object"
            raise GitHubRestError(
                msg,
                status_code=response.status_code,
                retriable=False,
                request_id=response.headers.get("x-github-request-id"),
            )
        new_id = created.get("id")
        if not isinstance(new_id, int):
            msg = "GitHub create comment response missing integer id"
            raise GitHubRestError(
                msg,
                status_code=response.status_code,
                retriable=False,
                request_id=response.headers.get("x-github-request-id"),
            )
        return UpsertCommentResult(comment_id=new_id, updated=False)
    finally:
        if owns_client:
            client.close()
