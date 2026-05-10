"""Pure helpers that shape the LLM review output into a Reviews API payload.

No HTTP, no environment access, no GitHub API surface -- just data
transformations: severity-label rendering, anchor validation against
the parsed diff index, demotion of misaligned inline comments to the
review body, and the verdict-to-event mapping.

These helpers live in their own module because:

* Keeping them I/O-free makes them trivial to unit-test (the bulk of
  ``tests/test_post_pr_llm_review.py`` exercises this surface).
* Splitting them out keeps :mod:`post_pr_llm_review` under the
  per-file LOC ceiling while leaving the orchestration layer
  responsible for the HTTP-side concerns.
"""

from __future__ import annotations

from typing import Final

from _pr_review_llm import DiffIndex, JsonObject, JsonValue

QUOTED_LINE_DISPLAY_LIMIT: Final[int] = 200

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
    """Render the markdown body of a single inline review comment.

    Args:
        body: The reviewer's prose; included verbatim.
        severity: Model severity (``must`` / ``should`` / ``nit``).
            Mapped through :data:`_SEVERITY_LABEL` for the bold prefix;
            unknown severities fall back to the raw value so a contract
            drift is visible in the rendered output rather than masked.
        quoted_line: The raw diff line the comment anchors to. Leading
            ``+``, ``-``, or `` `` markers are stripped (the GitHub UI
            already shows the diff context, so the marker is noise) and
            trailing whitespace / newline is removed. Lines longer than
            :data:`QUOTED_LINE_DISPLAY_LIMIT` are truncated and suffixed
            with a horizontal-ellipsis to keep review threads readable.

    Returns:
        A two-paragraph markdown string: ``**<Label>.** <body>`` followed
        by a fenced quote block of the (cleaned) anchor line.
    """

    label = _SEVERITY_LABEL.get(severity, severity)
    quoted = quoted_line
    if quoted.startswith(("+", "-", " ")):
        quoted = quoted[1:]
    quoted = quoted.rstrip("\n").rstrip()
    if len(quoted) > QUOTED_LINE_DISPLAY_LIMIT:
        quoted = quoted[:QUOTED_LINE_DISPLAY_LIMIT] + "\u2026"
    return f"**{label}.** {body}\n\n```\n{quoted}\n```"


def _split_inline_comments(
    raw_inline: list[JsonValue],
    diff_index: DiffIndex,
) -> tuple[list[JsonObject], list[JsonObject]]:
    """Partition model inline comments into (valid_for_github, demoted).

    Demoted entries are ones whose ``(path, line)`` is not present in
    the parsed diff index, plus malformed entries that do not match
    the inline-comment schema; they are re-emitted into the review
    body as general comments so feedback is not silently dropped when
    the model's anchor misses (which still happens despite the schema
    and anchor map).

    Args:
        raw_inline: The model's ``inline_comments`` array as a list of
            arbitrary :data:`JsonValue` (already loosely-typed because
            it comes back from OpenAI through ``json.loads``).
        diff_index: The right-side anchor index produced by
            :func:`_pr_review_llm.parse_diff_right_side`.

    Returns:
        A two-tuple ``(valid, demoted)`` where ``valid`` matches the
        Reviews API ``comments`` array shape (``path``, ``line``,
        ``side="RIGHT"``, ``body``) and ``demoted`` matches the
        general-comment shape (``severity``, ``body``).
    """

    valid: list[JsonObject] = []
    demoted: list[JsonObject] = []
    for entry in raw_inline:
        if not isinstance(entry, dict):
            demoted.append(
                {
                    "severity": "must",
                    "body": (
                        f"_(bot dropped a malformed inline comment: "
                        f"expected object, got `{type(entry).__name__}`)_"
                    ),
                }
            )
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
            preview = body if isinstance(body, str) else "(no body field)"
            demoted.append(
                {
                    "severity": severity if isinstance(severity, str) else "must",
                    "body": (
                        f"_(bot dropped a malformed inline comment; "
                        f"required fields missing or wrong type)_ {preview}"
                    ),
                }
            )
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
                        f"`{norm_path}:{line}` \u2014 {body} "
                        "_(originally inline; anchor not found in diff)_"
                    ),
                }
            )
    return valid, demoted


def _format_general_section(items: list[JsonObject], severity: str) -> str | None:
    """Render one severity-grouped bullet section for the review body.

    Args:
        items: Combined general-comment list (model ``general_comments``
            plus any inline entries demoted by
            :func:`_split_inline_comments`).
        severity: Severity to filter on (``must`` / ``should`` / ``nit``);
            entries with any other severity are skipped.

    Returns:
        A markdown block of the form ``**<Heading>**\\n- <body1>\\n- ...``
        or ``None`` if no item at that severity has a string body.
        ``None`` is the sentinel for "drop this section entirely" so
        the caller does not emit empty headings.
    """

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

    Inspects both inline and general comments from the model output,
    plus any inline entries that were demoted to general because their
    anchor failed validation.
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
    review: JsonObject, demoted: list[JsonObject], head_sha_marker: str
) -> str:
    """Assemble the markdown summary body posted to the Reviews API.

    Layout (sections joined with a blank line):

    1. ``**Verdict:** \\`<verdict>\\``` -- the model's top-level
       verdict, or ``comment`` if the field is missing or non-string.
    2. The ``summary`` paragraph from the model, when present.
    3. One severity-grouped bullet section per severity in
       :data:`_SEVERITY_ORDER` (only severities with at least one
       item are emitted; see :func:`_format_general_section`).
       Demoted inline comments are appended to the model's general
       comments before grouping so misaligned anchors still surface.
    4. The dedup marker passed in by the caller. The marker MUST be
       present so the next workflow run can find it and skip
       re-reviewing the same head SHA.

    Args:
        review: The validated model output.
        demoted: Inline comments that failed anchor validation in
            :func:`_split_inline_comments`.
        head_sha_marker: The dedup HTML-comment marker for the PR
            head SHA, produced by ``_marker(head_sha)`` in
            :mod:`_pr_review_http`. The marker is passed through
            (rather than re-built here) so this module stays free of
            HTTP-layer constants.

    Returns:
        The assembled markdown body string.
    """

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
    sections.append(head_sha_marker)
    return "\n\n".join(sections)


def _decide_event(review: JsonObject, demoted: list[JsonObject]) -> str:
    """Map review severities to a Reviews API ``event`` value.

    We never APPROVE from a bot -- that can satisfy branch protection
    requiring a review and let unreviewed code through.
    ``REQUEST_CHANGES`` is used when there is any ``must`` finding
    (inline, general, or demoted); otherwise ``COMMENT``.
    """

    return "REQUEST_CHANGES" if _has_must_severity(review, demoted) else "COMMENT"
