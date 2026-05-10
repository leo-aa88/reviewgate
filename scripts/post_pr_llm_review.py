#!/usr/bin/env python3
"""GitHub Actions helper: LLM PR review from diff, with per-head-SHA deduplication.

Triggered on ``pull_request`` (open/sync/reopen) and on a schedule to catch
missed webhooks. When ``OPENAI_API_KEY`` is unset, exits 0 without posting.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Final, TypeAlias

# Closed type for any value that survives a `json.loads` round-trip. We avoid
# `typing.Any` because the project rule requires every `Any` to be justified;
# JSON I/O is exactly the place where the precise shape is unknown but the set
# of possible values is closed, so `JsonValue` carries that intent in the type.
JsonValue: TypeAlias = (
    "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
)
JsonObject: TypeAlias = "dict[str, JsonValue]"

MARKER_PREFIX: Final[str] = "<!-- reviewgate-ai-review:sha="
MARKER_SUFFIX: Final[str] = " -->"
MAX_DIFF_CHARS: Final[int] = 120_000
GITHUB_API_VERSION: Final[str] = "2022-11-28"


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
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
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
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"GitHub API HTTP {exc.code} for {method} {url}: {detail[:800]}"
        ) from exc


def _list_open_pulls(owner: str, repo: str, token: str) -> list[JsonObject]:
    out: list[JsonObject] = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/pulls"
            f"?state=open&per_page=50&page={page}"
        )
        chunk = _http_json("GET", url, token)
        if not isinstance(chunk, list) or not chunk:
            break
        for row in chunk:
            if isinstance(row, dict):
                out.append(row)
        if len(chunk) < 50:
            break
        page += 1
    return out


def _fork_pr(repository: str, item: JsonObject) -> bool:
    head_value = item.get("head")
    head: JsonObject = head_value if isinstance(head_value, dict) else {}
    repo_value = head.get("repo")
    repo: JsonObject = repo_value if isinstance(repo_value, dict) else {}
    full = repo.get("full_name")
    return isinstance(full, str) and full != repository


def _list_issue_comments(
    owner: str, repo: str, issue_number: int, token: str
) -> list[JsonObject]:
    out: list[JsonObject] = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
            f"?per_page=100&page={page}"
        )
        chunk = _http_json("GET", url, token)
        if not isinstance(chunk, list) or not chunk:
            break
        for row in chunk:
            if isinstance(row, dict):
                out.append(row)
        if len(chunk) < 100:
            break
        page += 1
    return out


def _already_reviewed(comments: list[JsonObject], head_sha: str) -> bool:
    needle = _marker(head_sha)
    for c in comments:
        body = c.get("body")
        if isinstance(body, str) and needle in body:
            return True
    return False


def _get_pr_diff(owner: str, repo: str, pr_number: int, token: str) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    return _http_text("GET", url, token, accept="application/vnd.github.diff")


def _openai_review(diff_text: str, *, repo: str, pr_number: int) -> str:
    api_key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"

    system = (
        "You are a senior Python reviewer commenting on a GitHub pull request.\n"
        "\n"
        "You will be given a unified diff and nothing else. Ground every claim "
        "in text that appears in that diff. Do not invent files, symbols, "
        "imports, call sites, or behavior you cannot see. If a concern cannot "
        "be verified from the diff alone, either omit it or label it "
        "explicitly as `(unverified from diff)`.\n"
        "\n"
        "Do not comment on whole-file properties you cannot measure from a "
        "diff: file length, total line count, overall package layout, or "
        "anything outside the changed hunks. Review only the changed code.\n"
        "\n"
        "Standards to apply to the changed code:\n"
        "- Strict typing: any new `Any`, untyped parameter, or untyped return "
        "needs an inline justification in the code.\n"
        "- Public functions, classes, and modules introduced or modified by "
        "this diff have Google-style docstrings.\n"
        "- No silent backwards-compatibility shims; new compat paths must be "
        "explicitly justified.\n"
        "- No unexplained magic literals; constants get names.\n"
        "- Correctness, edge cases, and tests covering new branches.\n"
        "- Concurrency safety where shared state, async, or threading is "
        "touched.\n"
        "- KISS / DRY / SOLID applied to what the diff actually changes.\n"
        "\n"
        "Output: a single GitHub-flavored markdown comment, no preamble, no "
        "closing pleasantries, no restating of the PR title. Use this exact "
        "structure and omit any section that has no items:\n"
        "\n"
        "**Summary** — one or two sentences: what the PR does and your "
        "verdict (`approve`, `request changes`, or `nits only`).\n"
        "\n"
        "**Must-fix**\n"
        "- `path/to/file.py` — concise issue, quoting the offending line or "
        "hunk header from the diff; concrete fix.\n"
        "\n"
        "**Should-fix**\n"
        "- same format.\n"
        "\n"
        "**Nits**\n"
        "- same format.\n"
        "\n"
        "If the diff looks good, output only the Summary line with `approve` "
        "and one or two concrete reasons grounded in the diff."
    )
    user = f"Repository {repo}, PR #{pr_number}.\n\n```diff\n{diff_text}\n```"

    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        data = json.loads(resp.read().decode())
    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenAI response shape: {data!r}") from exc


def _post_issue_comment(owner: str, repo: str, issue_number: int, token: str, body: str) -> None:
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
    _http_json("POST", url, token, body={"body": body})


def _maybe_truncate(diff: str) -> str:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    head = diff[: MAX_DIFF_CHARS // 2]
    tail = diff[-MAX_DIFF_CHARS // 2 :]
    return (
        f"_Diff truncated to {MAX_DIFF_CHARS} characters for the model._\n\n"
        f"{head}\n\n[... omitted middle ...]\n\n{tail}"
    )


def _process_pr(owner: str, repo: str, repository: str, pr_number: int, token: str) -> str:
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

    comments = _list_issue_comments(owner, repo, pr_number, token)
    if _already_reviewed(comments, head_sha):
        return f"skip: already reviewed {head_sha[:7]}"

    if not os.environ.get("OPENAI_API_KEY"):
        print("::notice::OPENAI_API_KEY not set; skipping AI review (configure repo secret).")
        return "skip: no OPENAI_API_KEY"

    diff_raw = _get_pr_diff(owner, repo, pr_number, token)
    diff = _maybe_truncate(diff_raw)
    review = _openai_review(diff, repo=f"{owner}/{repo}", pr_number=pr_number)
    body = review + "\n\n" + _marker(head_sha)
    _post_issue_comment(owner, repo, pr_number, token, body)
    return f"posted review for {head_sha[:7]}"


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
