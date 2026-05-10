"""LLM-side helpers for ``post_pr_llm_review.py``.

This module owns everything that depends on the OpenAI API: the structured-
output JSON schema, the system prompt, the diff parser, and the chat call
itself. Keeping it separate lets the orchestration script stay under the
project's per-file LOC budget and lets the prompt/schema be edited without
touching GitHub API plumbing.

The diff parser builds the allowed-anchor map that the runtime uses to
validate inline comments before posting to GitHub's Reviews API, so a
hallucinated ``(path, line)`` anchor never reaches the wire.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.request
from typing import Final, TypeAlias

# Closed type for any value that survives a `json.loads` round-trip. We
# avoid `typing.Any` because the project rule requires every `Any` to be
# justified; JSON I/O is exactly the place where shape is unknown but the
# set of possible values is closed, so `JsonValue` carries that intent.
JsonValue: TypeAlias = (
    "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
)
JsonObject: TypeAlias = "dict[str, JsonValue]"

# `path -> set of valid RIGHT-side line numbers an inline comment can anchor to`.
DiffIndex: TypeAlias = "dict[str, set[int]]"

DEFAULT_MODEL: Final[str] = "gpt-5.4"
OPENAI_TIMEOUT_SECS: Final[int] = 240
LLM_MAX_OUTPUT_TOKENS: Final[int] = 4096


# Diff parsing -----------------------------------------------------------------

_HUNK_HEADER_RE: Final = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@"
)


def parse_diff_right_side(diff_text: str) -> DiffIndex:
    """Build a map of file path → RIGHT-side line numbers each hunk covers.

    Only context (``" "``) and added (``"+"``) lines on the RIGHT side are
    indexed: those are the only positions GitHub will accept for an inline
    review comment with ``side: RIGHT``. Pure-deletion, rename-only, and
    binary entries yield no entries (no RIGHT-side body to anchor to).

    Args:
        diff_text: Unified diff in the format returned by GitHub's
            ``application/vnd.github.diff`` accept header.

    Returns:
        Mapping of post-image file path (e.g. ``src/foo.py``) to the set of
        line numbers in that file that an inline review comment may target.
    """
    index: DiffIndex = {}
    current_path: str | None = None
    line_no = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current_path = None
            in_hunk = False
            continue
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                current_path = None
            elif target.startswith("b/"):
                current_path = target[2:]
                index.setdefault(current_path, set())
            else:
                current_path = target
                index.setdefault(current_path, set())
            in_hunk = False
            continue
        if raw.startswith("--- "):
            in_hunk = False
            continue
        m = _HUNK_HEADER_RE.match(raw)
        if m is not None:
            if current_path is None:
                in_hunk = False
                continue
            line_no = int(m.group("start"))
            in_hunk = True
            continue
        if not in_hunk or current_path is None:
            continue
        if not raw:
            # Blank body line is a context line on both sides; advance RIGHT.
            index[current_path].add(line_no)
            line_no += 1
            continue
        marker = raw[0]
        if marker in ("+", " "):
            index[current_path].add(line_no)
            line_no += 1
        elif marker == "-":
            # Consumed by LEFT side only; do not advance RIGHT counter.
            continue
        elif marker == "\\":
            # "\ No newline at end of file" — metadata, no line consumed.
            continue
        else:
            in_hunk = False
    return index


def format_anchor_map(diff_index: DiffIndex) -> str:
    """Render the per-file allowed-anchor index for the user prompt.

    The model uses this list to pick valid ``(path, line)`` targets; the
    runtime re-validates and demotes any out-of-range anchor before posting
    so the GitHub Reviews API never returns 422.
    """
    if not diff_index:
        return "(no RIGHT-side hunks in this diff)"
    parts: list[str] = []
    for path in sorted(diff_index):
        lines = sorted(diff_index[path])
        if not lines:
            continue
        ranges: list[str] = []
        run_start = lines[0]
        prev = lines[0]
        for n in lines[1:]:
            if n == prev + 1:
                prev = n
                continue
            ranges.append(
                f"{run_start}" if run_start == prev else f"{run_start}-{prev}"
            )
            run_start = n
            prev = n
        ranges.append(
            f"{run_start}" if run_start == prev else f"{run_start}-{prev}"
        )
        parts.append(f"- {path}: {', '.join(ranges)}")
    return "\n".join(parts) if parts else "(no RIGHT-side hunks in this diff)"


# Structured output schema ----------------------------------------------------

REVIEW_JSON_SCHEMA: Final[JsonObject] = {
    "name": "pr_review",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "summary", "inline_comments", "general_comments"],
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["request_changes", "comment"],
            },
            "summary": {"type": "string"},
            "inline_comments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "line", "severity", "body", "quoted_line"],
                    "properties": {
                        "path": {"type": "string"},
                        "line": {"type": "integer"},
                        "severity": {
                            "type": "string",
                            "enum": ["must", "should", "nit"],
                        },
                        "body": {"type": "string"},
                        "quoted_line": {"type": "string"},
                    },
                },
            },
            "general_comments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["severity", "body"],
                    "properties": {
                        "severity": {
                            "type": "string",
                            "enum": ["must", "should", "nit"],
                        },
                        "body": {"type": "string"},
                    },
                },
            },
        },
    },
}


SYSTEM_PROMPT: Final[str] = """\
You are a senior Python reviewer for a strict, type-disciplined codebase \
(Python 3.12, mypy --strict semantics, Google-style docstrings on public \
APIs, pyproject-managed). You receive a unified diff. Your job is to find \
real defects and force fixes. Treat the patch as a hostile submission: \
assume the author is competent but cutting corners, missing edge cases, \
or papering over deeper issues. This bot never approves; verdict is \
either `request_changes` (any `must` finding) or `comment` (only \
`should` / `nit` findings). Branch protection that requires a human \
review must not be satisfied by this bot.

Hard constraints
- Every claim must be grounded in a line that appears in the diff. For \
inline comments, fill `quoted_line` with the exact text of the line from \
the diff (no leading '+', no edits, no reflow).
- If a concern cannot be verified from this diff alone, drop it. Do not \
hedge with disclaimers; just drop it.
- Do not comment on whole-file properties you cannot measure from a diff \
(file length, total LOC, package layout, anything outside the changed \
hunks).
- Inline comments must target a `path` and `line` that appear in the \
allowed-anchors map provided in the user message. If you cannot anchor a \
concern, put it in `general_comments` instead.
- Prefer inline over general. Use `general_comments` only for true \
cross-cutting concerns (e.g. missing test for a new branch).
- Write each `body` as: <one-sentence problem>. <one concrete fix with \
code or a specific instruction>. Never write "consider" or "you might \
want to"; say "do X".
- No filler. Empty arrays are fine. Do not pad with nits to look thorough.

Severity = must (blocks merge)
- New `Any`, untyped param/return, broad `except`, silent fallback, or \
`# type: ignore` without an inline justification.
- Public function/class/module added or modified without a Google-style \
docstring.
- Magic literal without a named constant or rationale comment.
- Concurrency hazard: shared mutable state, async without cancellation, \
missing locks/guards, race in tests.
- Test that does not exercise the new branch, snapshot-only test for \
logic, missing failure-path coverage.
- Backwards-compat shim with no justification, dead branch, unreachable \
code.
- Behavior change not called out in the PR (rename, signature change, \
exception type change, JSON schema drift).
- Anything that risks data loss, security regression, or a production \
outage.

Severity = should
- DRY/SOLID violation the diff actually introduces.
- Naming that hides intent.
- Test that passes for the wrong reason.

Severity = nit
- Pure style, no behavior change. Be sparing.

Verdict
- `request_changes` if there is any `must`.
- `comment` if there are only `should` or `nit` items, or no findings.

Output JSON only, matching the supplied schema. No prose outside the JSON.
"""


# OpenAI call -----------------------------------------------------------------


def call_openai_review(
    diff_text: str,
    *,
    repo: str,
    pr_number: int,
    diff_index: DiffIndex,
) -> JsonObject:
    """Call the configured OpenAI chat-completions endpoint and return JSON.

    The call uses ``response_format = json_schema`` so the model cannot
    drift into prose; the returned dict matches `REVIEW_JSON_SCHEMA`. The
    caller is responsible for re-validating inline-comment anchors against
    ``diff_index``.

    Args:
        diff_text: Unified diff (possibly truncated for the model's input
            window). The full diff is what the model reasons over.
        repo: ``owner/name`` slug, included in the user prompt for context.
        pr_number: Pull request number, included in the user prompt.
        diff_index: Mapping of valid RIGHT-side anchors, rendered into the
            user prompt so the model knows which anchors will be accepted.

    Returns:
        Parsed JSON object matching `REVIEW_JSON_SCHEMA`.

    Raises:
        RuntimeError: If the OpenAI response is missing the expected
            ``choices[0].message.content`` field or that content does not
            decode as a JSON object.
        KeyError: If ``OPENAI_API_KEY`` is not set in the environment.
    """
    api_key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    base = (
        os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    ).rstrip("/")
    url = f"{base}/chat/completions"

    user = (
        f"Repository: {repo}\n"
        f"PR: #{pr_number}\n"
        "\n"
        "Allowed inline-comment anchors (path: RIGHT-side line numbers).\n"
        "Inline comments outside this set will be dropped before posting.\n"
        "\n"
        f"{format_anchor_map(diff_index)}\n"
        "\n"
        "Unified diff follows.\n"
        "\n"
        f"```diff\n{diff_text}\n```"
    )

    payload: JsonObject = {
        "model": model,
        "max_completion_tokens": LLM_MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": REVIEW_JSON_SCHEMA,
        },
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST"
    )
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=OPENAI_TIMEOUT_SECS, context=ctx) as resp:
        data = json.loads(resp.read().decode())
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenAI response shape: {data!r}") from exc
    parsed = json.loads(content) if isinstance(content, str) else content
    if not isinstance(parsed, dict):
        raise RuntimeError(f"OpenAI returned non-object content: {content!r}")
    return parsed
