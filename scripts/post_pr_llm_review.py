#!/usr/bin/env python3
"""GitHub Actions helper: LLM PR review from diff, with per-head-SHA dedup.

The script runs on ``pull_request`` (open/sync/reopen/ready_for_review) and
on a schedule to catch missed webhooks. When ``OPENAI_API_KEY`` is unset,
it exits 0 without posting.

Reviews are posted via the GitHub Pull Request Reviews API with inline
review comments anchored to specific files and lines on the RIGHT side of
the diff, so agents and humans see per-hunk feedback in the Files tab. A
summary body carries the head-SHA marker used for deduplication.

LLM concerns (prompt, schema, OpenAI call, diff parser) live in the sibling
module :mod:`_pr_review_llm`; this file owns GitHub API I/O, dedup, payload
construction, and the CLI entrypoint.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Final

from _pr_review_llm import (
    DiffIndex,
    JsonObject,
    JsonValue,
    call_openai_review,
    parse_diff_right_side,
)

MARKER_PREFIX: Final[str] = "<!-- reviewgate-ai-review:sha="
MARKER_SUFFIX: Final[str] = " -->"
MAX_DIFF_CHARS: Final[int] = 120_000
GITHUB_API_VERSION: Final[str] = "2022-11-28"
HTTP_TIMEOUT_SECS: Final[int] = 120
ISSUE_COMMENTS_PAGE_SIZE: Final[int] = 100
PULLS_PAGE_SIZE: Final[int] = 50
REVIEWS_PAGE_SIZE: Final[int] = 100
QUOTED_LINE_DISPLAY_LIMIT: Final[int] = 200


def _marker(sha: str) -> str:
    return f"{MARKER_PREFIX}{sha}{MARKER_SUFFIX}"


def _github_headers(token: str, *, accept: str) -> dict[str, str]:
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


def _list_paginated(
    url_template: str, token: str, *, page_size: int
) -> list[JsonObject]:
    """Walk a paginated GitHub list endpoint to exhaustion.

    Args:
        url_template: A URL containing a single ``{page}`` placeholder; the
            ``per_page`` query parameter is expected to already be baked in.
        token: GitHub bearer token.
        page_size: Page size encoded in the URL; used to detect the last page.
    """
    out: list[JsonObject] = []
    page = 1
    while True:
        chunk = _http_json("GET", url_template.format(page=page), token)
        if not isinstance(chunk, list) or not chunk:
            break
        for row in chunk:
            if isinstance(row, dict):
                out.append(row)
        if len(chunk) < page_size:
            break
        page += 1
    return out


def _list_open_pulls(owner: str, repo: str, token: str) -> list[JsonObject]:
    tmpl = (
        f"https://api.github.com/repos/{owner}/{repo}/pulls"
        f"?state=open&per_page={PULLS_PAGE_SIZE}&page={{page}}"
    )
    return _list_paginated(tmpl, token, page_size=PULLS_PAGE_SIZE)


def _list_issue_comments(
    owner: str, repo: str, issue_number: int, token: str
) -> list[JsonObject]:
    tmpl = (
        f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
        f"?per_page={ISSUE_COMMENTS_PAGE_SIZE}&page={{page}}"
    )
    return _list_paginated(tmpl, token, page_size=ISSUE_COMMENTS_PAGE_SIZE)


def _list_pr_reviews(
    owner: str, repo: str, pr_number: int, token: str
) -> list[JsonObject]:
    tmpl = (
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        f"?per_page={REVIEWS_PAGE_SIZE}&page={{page}}"
    )
    return _list_paginated(tmpl, token, page_size=REVIEWS_PAGE_SIZE)


def _fork_pr(repository: str, item: JsonObject) -> bool:
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

    Issue comments are still scanned to preserve compatibility with reviews
    posted by older versions of this script (which used issue comments
    rather than the Reviews API).
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
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    return _http_text("GET", url, token, accept="application/vnd.github.diff")


# Review payload construction -------------------------------------------------

_SEVERITY_LABEL: Final[dict[str, str]] = {
    "must": "Must-fix",
    "should": "Should-fix",
    "nit": "Nit",
}
_SEVERITY_HEADING: Final[dict[str, str]] = {
    "must": "Must-fix",
    "should": "Should-fix",
    "nit": "Nits",
}
_SEVERITY_ORDER: Final[tuple[str, ...]] = ("must", "should", "nit")


def _normalize_path(path: str) -> str:
    """Strip diff path prefixes the model sometimes echoes back."""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _format_inline_body(body: str, severity: str, quoted_line: str) -> str:
    label = _SEVERITY_LABEL.get(severity, severity)
    quoted = quoted_line
    if quoted.startswith(("+", "-", " ")):
        quoted = quoted[1:]
    quoted = quoted.rstrip("\n").rstrip()
    if len(quoted) > QUOTED_LINE_DISPLAY_LIMIT:
        quoted = quoted[:QUOTED_LINE_DISPLAY_LIMIT] + "…"
    return f"**{label}.** {body}\n\n```\n{quoted}\n```"


def _split_inline_comments(
    raw_inline: list[JsonValue],
    diff_index: DiffIndex,
) -> tuple[list[JsonObject], list[JsonObject]]:
    """Partition model inline comments into (valid_for_github, demoted).

    Demoted entries are ones whose ``(path, line)`` is not present in the
    parsed diff index; they are re-emitted into the review body as general
    comments so feedback is not silently dropped when the model's anchor
    misses (which still happens despite the schema and anchor map).
    """
    valid: list[JsonObject] = []
    demoted: list[JsonObject] = []
    for entry in raw_inline:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        line = entry.get("line")
        body = entry.get("body")
        severity = entry.get("severity")
        quoted = entry.get("quoted_line")
        if not (
            isinstance(path, str)
            and isinstance(line, int)
            and isinstance(body, str)
            and isinstance(severity, str)
            and isinstance(quoted, str)
        ):
            continue
        norm_path = _normalize_path(path)
        if norm_path in diff_index and line in diff_index[norm_path]:
            valid.append(
                {
                    "path": norm_path,
                    "line": line,
                    "side": "RIGHT",
                    "body": _format_inline_body(body, severity, quoted),
                }
            )
        else:
            demoted.append(
                {
                    "severity": severity,
                    "body": (
                        f"`{norm_path}:{line}` — {body} "
                        "_(originally inline; anchor not found in diff)_"
                    ),
                }
            )
    return valid, demoted


def _format_general_section(items: list[JsonObject], severity: str) -> str | None:
    rows = [
        str(i.get("body"))
        for i in items
        if i.get("severity") == severity and isinstance(i.get("body"), str)
    ]
    if not rows:
        return None
    bullets = "\n".join(f"- {body}" for body in rows)
    return f"**{_SEVERITY_HEADING[severity]}**\n{bullets}"


def _has_must_severity(review: JsonObject, demoted: list[JsonObject]) -> bool:
    """Return True if any item across the review has severity ``must``.

    Inspects both inline and general comments from the model output, plus
    any inline entries that were demoted to general because their anchor
    failed validation.
    """
    for key in ("inline_comments", "general_comments"):
        items = review.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("severity") == "must":
                return True
    return any(d.get("severity") == "must" for d in demoted)


def _build_review_body(
    review: JsonObject, demoted: list[JsonObject], head_sha: str
) -> str:
    summary = review.get("summary")
    verdict = review.get("verdict")
    summary_text = summary if isinstance(summary, str) else ""
    verdict_text = verdict if isinstance(verdict, str) else "comment"

    raw_general = review.get("general_comments")
    general_items: list[JsonObject] = (
        [g for g in raw_general if isinstance(g, dict)]
        if isinstance(raw_general, list)
        else []
    )
    combined: list[JsonObject] = general_items + demoted

    sections: list[str] = [f"**Verdict:** `{verdict_text}`"]
    if summary_text:
        sections.append(summary_text)
    for sev in _SEVERITY_ORDER:
        block = _format_general_section(combined, sev)
        if block is not None:
            sections.append(block)
    sections.append(_marker(head_sha))
    return "\n\n".join(sections)


def _decide_event(review: JsonObject, demoted: list[JsonObject]) -> str:
    """Map review severities to a Reviews API ``event`` value.

    We never APPROVE from a bot — that can satisfy branch protection
    requiring a review and let unreviewed code through. ``REQUEST_CHANGES``
    is used when there is any ``must`` finding (inline, general, or
    demoted); otherwise ``COMMENT``.
    """
    return "REQUEST_CHANGES" if _has_must_severity(review, demoted) else "COMMENT"


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


# Top-level orchestration ------------------------------------------------------


def _maybe_truncate(diff: str) -> str:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    head = diff[: MAX_DIFF_CHARS // 2]
    tail = diff[-MAX_DIFF_CHARS // 2 :]
    return (
        f"_Diff truncated to {MAX_DIFF_CHARS} characters for the model._\n\n"
        f"{head}\n\n[... omitted middle ...]\n\n{tail}"
    )


def _process_pr(
    owner: str, repo: str, repository: str, pr_number: int, token: str
) -> str:
    item = _http_json(
        "GET",
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
        token,
    )
    if not isinstance(item, dict):
        return "skip: unexpected pull response"

    if _fork_pr(repository, item):
        return "skip: fork PR"

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
    diff_for_model = _maybe_truncate(diff_raw)

    review = call_openai_review(
        diff_for_model,
        repo=f"{owner}/{repo}",
        pr_number=pr_number,
        diff_index=diff_index,
    )
    raw_inline = review.get("inline_comments")
    inline_list: list[JsonValue] = (
        list(raw_inline) if isinstance(raw_inline, list) else []
    )
    valid_inline, demoted = _split_inline_comments(inline_list, diff_index)
    body = _build_review_body(review, demoted, head_sha)
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
        f"(inline={len(valid_inline)}, demoted={len(demoted)})"
    )


def _event_pull_request() -> tuple[str, str, str, int] | None:
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
