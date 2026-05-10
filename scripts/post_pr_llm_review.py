#!/usr/bin/env python3
"""GitHub Actions helper: LLM PR review from diff, with per-head-SHA dedup.

The script runs on ``pull_request`` (open/sync/reopen/ready_for_review)
and on a schedule to catch missed webhooks. When ``OPENAI_API_KEY`` is
unset, it exits 0 without posting.

Reviews are posted via the GitHub Pull Request Reviews API with inline
review comments anchored to specific files and lines on the RIGHT side
of the diff, so agents and humans see per-hunk feedback in the Files
tab. A summary body carries the head-SHA marker used for deduplication.

Module split (each file stays under the per-file LOC ceiling):

* :mod:`_pr_review_llm` -- prompt, JSON schema, OpenAI HTTP call,
  unified-diff parser.
* :mod:`_pr_review_http` -- low-level GitHub HTTP client
  (``_http_json``, ``_http_text``) and the dedup marker helper.
* :mod:`_pr_review_payload` -- pure helpers that shape the LLM
  response into a Reviews API payload.
* This module -- pagination + dedup + Reviews POST + the CLI
  orchestration that ties everything together.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final

# Make sibling imports work no matter how the script is invoked
# (``python scripts/post_pr_llm_review.py`` from repo root,
# ``python -m post_pr_llm_review`` from ``scripts/``, etc.).
# Direct execution puts the script's directory at ``sys.path[0]``;
# module-style execution does not. Inserting it unconditionally is a
# no-op when it is already present.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _pr_review_http import (  # noqa: E402  (sys.path mutation above)
    GITHUB_API_VERSION,
    HTTP_TIMEOUT_SECS,
    MARKER_PREFIX,
    MARKER_SUFFIX,
    _github_headers,
    _http_json,
    _http_text,
    _marker,
)
from _pr_review_llm import (  # noqa: E402
    DiffIndex,
    JsonObject,
    JsonValue,
    call_openai_review,
    parse_diff_right_side,
)
from _pr_review_payload import (  # noqa: E402
    MIN_EVIDENCE_LEN,
    QUOTED_LINE_DISPLAY_LIMIT,
    _build_review_body,
    _decide_event,
    _downgrade_coverage_musts,
    _filter_general_comments,
    _format_general_section,
    _format_inline_body,
    _has_must_severity,
    _normalize_path,
    _split_inline_comments,
)

# Re-export everything imported above so ``import post_pr_llm_review``
# remains the single public entry point used by the test suite and by
# any downstream tooling that drives the script as a library.
__all__ = [
    "DiffIndex",
    "GITHUB_API_VERSION",
    "HTTP_TIMEOUT_SECS",
    "JsonObject",
    "JsonValue",
    "MARKER_PREFIX",
    "MARKER_SUFFIX",
    "MAX_DIFF_CHARS",
    "MIN_EVIDENCE_LEN",
    "QUOTED_LINE_DISPLAY_LIMIT",
    "call_openai_review",
    "main",
    "parse_diff_right_side",
]

# Sized to fit a typical large multi-feature PR without head/tail
# cropping. At ~4 chars/token this is ~62k tokens of diff, comfortably
# inside the 128k input window of `gpt-5.4` / `gpt-4o` plus ~3k of
# prompt overhead and the 4096-token completion cap. Cropping
# silently degrades review quality (the model fills the missing
# middle with generic-feeling concerns), so the budget is generous;
# `_truncation_notice` still surfaces a banner when even this is
# exceeded so reviewers know they got a partial view.
MAX_DIFF_CHARS: Final[int] = 250_000
ISSUE_COMMENTS_PAGE_SIZE: Final[int] = 100
PULLS_PAGE_SIZE: Final[int] = 50
REVIEWS_PAGE_SIZE: Final[int] = 100


# Pagination + dedup ----------------------------------------------------------


def _list_paginated(
    base_url: str, token: str, *, page_size: int
) -> list[JsonObject]:
    """Walk a paginated GitHub list endpoint to exhaustion.

    Args:
        base_url: Fully-formed URL with everything except ``page=``
            already baked in (caller is expected to include
            ``per_page``). Pagination appends ``&page=N`` -- using
            direct string concatenation rather than
            :py:meth:`str.format` so any literal ``{`` / ``}``
            characters in a query value cannot trigger a ``KeyError``
            before the HTTP call.
        token: GitHub bearer token.
        page_size: Page size encoded in the URL; used to detect the
            last page.

    Raises:
        RuntimeError: If a page returns a non-list payload (e.g. an
            error envelope leaked past :func:`_http_json`). The
            scan must NOT silently treat that as end-of-pagination
            because it would let :func:`_already_reviewed` miss an
            existing dedup marker and post duplicate reviews.
    """

    out: list[JsonObject] = []
    page = 1
    separator = "&" if "?" in base_url else "?"
    while True:
        url = f"{base_url}{separator}page={page}"
        chunk = _http_json("GET", url, token)
        if not isinstance(chunk, list):
            raise RuntimeError(
                f"Expected list from {url}, got "
                f"{type(chunk).__name__}: {chunk!r}"
            )
        if not chunk:
            break
        for row in chunk:
            if isinstance(row, dict):
                out.append(row)
        if len(chunk) < page_size:
            break
        page += 1
    return out


def _list_open_pulls(owner: str, repo: str, token: str) -> list[JsonObject]:
    """List the repo's open pull requests, following pagination."""

    base = (
        f"https://api.github.com/repos/{owner}/{repo}/pulls"
        f"?state=open&per_page={PULLS_PAGE_SIZE}"
    )
    return _list_paginated(base, token, page_size=PULLS_PAGE_SIZE)


def _list_issue_comments(
    owner: str, repo: str, issue_number: int, token: str
) -> list[JsonObject]:
    """List the issue/PR conversation comments."""

    base = (
        f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
        f"?per_page={ISSUE_COMMENTS_PAGE_SIZE}"
    )
    return _list_paginated(base, token, page_size=ISSUE_COMMENTS_PAGE_SIZE)


def _list_pr_reviews(
    owner: str, repo: str, pr_number: int, token: str
) -> list[JsonObject]:
    """List the PR's submitted reviews (the dedup-marker source of truth)."""

    base = (
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        f"?per_page={REVIEWS_PAGE_SIZE}"
    )
    return _list_paginated(base, token, page_size=REVIEWS_PAGE_SIZE)


def _fork_pr(repository: str, item: JsonObject) -> bool:
    """Return True when ``item`` describes a PR from a fork.

    Forks cannot be commented on with the workflow's ``GITHUB_TOKEN``
    (Actions secrets are scoped out per GitHub's security model), so
    the orchestrator skips them rather than failing the whole run.
    """

    head_value = item.get("head")
    head: JsonObject = head_value if isinstance(head_value, dict) else {}
    repo_value = head.get("repo")
    repo: JsonObject = repo_value if isinstance(repo_value, dict) else {}
    full = repo.get("full_name")
    return isinstance(full, str) and full != repository


def _already_reviewed(
    issue_comments: list[JsonObject],
    reviews: list[JsonObject],
    head_sha: str,
) -> bool:
    """Return True if any prior comment or review summary carries the marker.

    Issue comments are still scanned to preserve compatibility with
    reviews posted by older versions of this script (which used issue
    comments rather than the Reviews API).
    """

    needle = _marker(head_sha)
    for c in issue_comments:
        body = c.get("body")
        if isinstance(body, str) and needle in body:
            return True
    for r in reviews:
        body = r.get("body")
        if isinstance(body, str) and needle in body:
            return True
    return False


def _get_pr_diff(owner: str, repo: str, pr_number: int, token: str) -> str:
    """Fetch the PR's unified diff via the GitHub diff accept type."""

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    return _http_text("GET", url, token, accept="application/vnd.github.diff")


# Reviews POST ----------------------------------------------------------------


_ALLOWED_REVIEW_EVENTS: frozenset[str] = frozenset({"COMMENT", "REQUEST_CHANGES"})
"""Reviews API ``event`` values this script is allowed to post.

We deliberately exclude ``APPROVE`` (a bot APPROVE could satisfy
branch protection requiring a review and let unreviewed code through)
and ``PENDING`` (drafts; this script always posts immediately).
Validating against this allowlist before the HTTP call turns a
malformed ``review["verdict"]`` into a clear local error instead of
an opaque GitHub 422 mid-run.
"""


def _post_pr_review(
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    *,
    head_sha: str,
    body: str,
    event: str,
    comments: list[JsonObject],
) -> None:
    """POST a pull-request review to the GitHub Reviews API.

    Args:
        owner: Repository owner login.
        repo: Repository name.
        pr_number: Pull request number on ``owner/repo``.
        token: GitHub bearer token with ``pull-requests: write`` scope.
        head_sha: Full SHA of the commit the review applies to. Sent
            as ``commit_id`` so the review is anchored to a specific
            head and cannot be misattributed if the PR is force-pushed
            mid-review.
        body: Markdown body of the review (see :func:`_build_review_body`).
        event: Reviews API event verb. Must be one of
            :data:`_ALLOWED_REVIEW_EVENTS`; any other value is rejected
            locally to avoid an opaque GitHub 422 mid-run and to
            prevent a bot ``APPROVE`` from satisfying branch
            protection.
        comments: Inline review comments already validated by
            :func:`_split_inline_comments`. Each entry must carry
            ``path``, ``line``, ``side`` (``"RIGHT"``), and ``body``.

    Raises:
        RuntimeError: If ``event`` is not in
            :data:`_ALLOWED_REVIEW_EVENTS`. HTTP-level failures from
            the Reviews POST propagate from :func:`_http_json` as
            ``RuntimeError`` with the GitHub response detail attached.
    """

    if event not in _ALLOWED_REVIEW_EVENTS:
        raise RuntimeError(
            f"Refusing to post PR review with event={event!r}; "
            f"expected one of {sorted(_ALLOWED_REVIEW_EVENTS)}"
        )
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    inline: list[JsonValue] = []
    inline.extend(comments)
    payload: JsonObject = {
        "commit_id": head_sha,
        "event": event,
        "body": body,
        "comments": inline,
    }
    _http_json("POST", url, token, body=payload)


# Top-level orchestration -----------------------------------------------------


def _maybe_truncate(diff: str) -> tuple[str, bool]:
    """Truncate oversized diffs around the head/tail to fit the model window.

    Returns:
        ``(diff_for_model, was_truncated)``. The boolean lets the
        orchestrator surface a notice in the review body so a human
        reviewer can tell when findings were generated against an
        incomplete view of the patch -- a known source of bot
        false-negatives (fix not visible because it lives in the
        omitted middle) and false-positives (claim grounded in the
        head/tail that contradicts something in the omitted middle).
    """

    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    head = diff[: MAX_DIFF_CHARS // 2]
    tail = diff[-MAX_DIFF_CHARS // 2 :]
    truncated = (
        f"_Diff truncated to {MAX_DIFF_CHARS} characters for the model._\n\n"
        f"{head}\n\n[... omitted middle ...]\n\n{tail}"
    )
    return truncated, True


def _truncation_notice(was_truncated: bool, original_len: int) -> str | None:
    """Render the body-level truncation banner, or ``None`` to skip it.

    Args:
        was_truncated: Output flag from :func:`_maybe_truncate`.
        original_len: Length of the diff BEFORE truncation, in chars.
            Included so reviewers can gauge how much was dropped
            relative to the :data:`MAX_DIFF_CHARS` budget.

    Returns:
        A markdown italic line for prepending to the review body, or
        ``None`` when no truncation occurred (caller renders the body
        unchanged).
    """

    if not was_truncated:
        return None
    return (
        f"_Note: this diff was {original_len} chars; the LLM saw a "
        f"{MAX_DIFF_CHARS}-char head/tail crop. Findings outside that "
        f"window are not present in the review below._"
    )


def _process_pr(
    owner: str, repo: str, repository: str, pr_number: int, token: str
) -> str:
    """Drive one PR through the dedup -> LLM -> post pipeline.

    Returns a short status string describing the outcome (``skip:
    ...`` or ``posted ...``) so the workflow log carries enough
    context to debug a particular run without re-fetching the API.
    """

    item = _http_json(
        "GET",
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
        token,
    )
    if not isinstance(item, dict):
        return "skip: unexpected pull response"

    if _fork_pr(repository, item):
        return "skip: fork PR"

    draft = item.get("draft")
    if isinstance(draft, bool) and draft:
        return "skip: draft PR"

    head_value = item.get("head")
    head: JsonObject = head_value if isinstance(head_value, dict) else {}
    head_sha = head.get("sha")
    if not isinstance(head_sha, str) or len(head_sha) < 7:
        return "skip: missing head.sha"

    issue_comments = _list_issue_comments(owner, repo, pr_number, token)
    reviews = _list_pr_reviews(owner, repo, pr_number, token)
    if _already_reviewed(issue_comments, reviews, head_sha):
        return f"skip: already reviewed {head_sha[:7]}"

    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "::notice::OPENAI_API_KEY not set; skipping AI review "
            "(configure repo secret)."
        )
        return "skip: no OPENAI_API_KEY"

    diff_raw = _get_pr_diff(owner, repo, pr_number, token)
    diff_index = parse_diff_right_side(diff_raw)
    diff_for_model, was_truncated = _maybe_truncate(diff_raw)

    review = call_openai_review(
        diff_for_model,
        repo=f"{owner}/{repo}",
        pr_number=pr_number,
        diff_index=diff_index,
    )
    review, dropped_general = _filter_general_comments(review, diff_for_model)
    for d in dropped_general:
        reason = d.get("_drop_reason", "unknown")
        snippet = str(d.get("body", ""))[:160].replace("\n", " ")
        print(f"::warning::Bot self-check dropped general comment ({reason}): {snippet}")
    review, downgraded_general = _downgrade_coverage_musts(review)
    for d in downgraded_general:
        snippet = str(d.get("body", ""))[:160].replace("\n", " ")
        print(
            f"::warning::Bot self-check downgraded must -> should "
            f"(test-coverage gap): {snippet}"
        )
    raw_inline = review.get("inline_comments")
    inline_list: list[JsonValue] = (
        list(raw_inline) if isinstance(raw_inline, list) else []
    )
    valid_inline, demoted = _split_inline_comments(inline_list, diff_index)
    body = _build_review_body(
        review,
        demoted,
        _marker(head_sha),
        truncation_notice=_truncation_notice(was_truncated, len(diff_raw)),
    )
    event = _decide_event(review, demoted)
    _post_pr_review(
        owner,
        repo,
        pr_number,
        token,
        head_sha=head_sha,
        body=body,
        event=event,
        comments=valid_inline,
    )
    return (
        f"posted {event.lower()} review for {head_sha[:7]} "
        f"(inline={len(valid_inline)}, demoted={len(demoted)}, "
        f"dropped_general={len(dropped_general)}, "
        f"downgraded_general={len(downgraded_general)})"
    )


def _event_pull_request() -> tuple[str, str, str, int] | None:
    """Parse ``GITHUB_EVENT_PATH`` for a ``pull_request`` event payload.

    Returns ``(owner, repo, full_name, pr_number)`` on success, or
    ``None`` when the event file is missing / malformed -- the caller
    treats that as a non-recoverable input error.
    """

    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        loaded: JsonValue = json.load(f)
    if not isinstance(loaded, dict):
        return None
    pr_value = loaded.get("pull_request")
    repo_value = loaded.get("repository")
    if not isinstance(pr_value, dict) or not isinstance(repo_value, dict):
        return None
    full = repo_value.get("full_name")
    num = pr_value.get("number")
    if not isinstance(full, str) or "/" not in full or not isinstance(num, int):
        return None
    owner, name = full.split("/", 1)
    return owner, name, full, num


def main() -> int:
    """CLI entrypoint invoked by ``.github/workflows/pr-llm-review.yml``."""

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("::error::GITHUB_TOKEN is required", file=sys.stderr)
        return 1

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    if event_name == "pull_request":
        parsed = _event_pull_request()
        if not parsed:
            print("::error::Could not parse pull_request event", file=sys.stderr)
            return 1
        owner, repo, repository, pr_number = parsed
        msg = _process_pr(owner, repo, repository, pr_number, token)
        print(msg)
        return 0

    if event_name in ("schedule", "workflow_dispatch"):
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if "/" not in repository:
            print("::error::GITHUB_REPOSITORY missing", file=sys.stderr)
            return 1
        owner, name = repository.split("/", 1)
        pulls = _list_open_pulls(owner, name, token)
        if not pulls:
            print("idle: no open pull requests")
            return 0
        for item in pulls:
            num = item.get("number")
            if not isinstance(num, int):
                continue
            if _fork_pr(repository, item):
                print(f"PR #{num}: skip fork")
                continue
            msg = _process_pr(owner, name, repository, num, token)
            print(f"PR #{num}: {msg}")
        return 0

    print(f"::notice::Unsupported event {event_name!r}; no-op.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
