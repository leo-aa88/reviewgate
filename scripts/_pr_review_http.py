"""Low-level GitHub HTTP client for the PR-review automation.

This module owns *only* the bytes-on-the-wire concerns: building the
request, attaching the bearer token and the GitHub API version
header, decoding the response, and translating ``HTTPError`` into a
``RuntimeError`` with enough detail to debug a failing workflow run.

Higher-level concerns -- pagination, dedup, payload construction,
review posting, orchestration -- live in
:mod:`post_pr_llm_review` and :mod:`_pr_review_payload`. Splitting
keeps each module under the project's per-file LOC ceiling and lets
``post_pr_llm_review`` re-export the helpers test code monkey-patches.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Final

from _pr_review_llm import JsonObject, JsonValue

MARKER_PREFIX: Final[str] = "<!-- reviewgate-ai-review:sha="
MARKER_SUFFIX: Final[str] = " -->"
GITHUB_API_VERSION: Final[str] = "2022-11-28"
HTTP_TIMEOUT_SECS: Final[int] = 120


def _marker(sha: str) -> str:
    """Return the dedup HTML-comment marker carrying the head SHA."""

    return f"{MARKER_PREFIX}{sha}{MARKER_SUFFIX}"


def _github_headers(token: str, *, accept: str) -> dict[str, str]:
    """Build the standard request headers for a GitHub REST call."""

    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "reviewgate-core-pr-review-script",
    }


def _http_json(
    method: str,
    url: str,
    token: str,
    *,
    accept: str = "application/vnd.github+json",
    body: JsonObject | None = None,
) -> JsonValue:
    """Perform a JSON GitHub API call and return the decoded response.

    Args:
        method: HTTP verb (``GET``, ``POST``, ...).
        url: Fully-formed request URL.
        token: GitHub bearer token.
        accept: ``Accept`` header value; defaults to the standard
            JSON accept type.
        body: Optional JSON body for write methods. When provided,
            ``Content-Type: application/json`` is added automatically.

    Returns:
        Decoded JSON value, or ``None`` if the response body is empty.

    Raises:
        RuntimeError: When the request returns a non-2xx status. A
            403 response gets an extra hint about the
            ``pull-requests: write`` workflow permission, since that
            is the most common cause for org-policy-blocked tokens.
    """

    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in _github_headers(token, accept=accept).items():
        req.add_header(k, v)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECS, context=ctx) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        hint = ""
        if exc.code == 403:
            hint = (
                " Hint: ensure workflow permissions include pull-requests:write; "
                "repo Settings → Actions → General → Workflow permissions must allow "
                "read/write (org policy can block this)."
            )
        raise RuntimeError(
            f"GitHub API HTTP {exc.code} for {method} {url}: {detail[:800]}{hint}"
        ) from exc
    return json.loads(raw) if raw.strip() else None


def _http_text(method: str, url: str, token: str, *, accept: str) -> str:
    """Perform a GitHub API call returning a raw text body (e.g. a diff).

    Args:
        method: HTTP verb.
        url: Fully-formed request URL.
        token: GitHub bearer token.
        accept: ``Accept`` header value -- callers pass
            ``application/vnd.github.diff`` for unified-diff fetches.

    Returns:
        The raw decoded response body.

    Raises:
        RuntimeError: On non-2xx responses, with the GitHub error
            detail truncated to 800 characters.
    """

    req = urllib.request.Request(url, method=method)
    for k, v in _github_headers(token, accept=accept).items():
        req.add_header(k, v)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECS, context=ctx) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"GitHub API HTTP {exc.code} for {method} {url}: {detail[:800]}"
        ) from exc
