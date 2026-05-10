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

import re
from typing import Final

from _pr_review_llm import DiffIndex, JsonObject, JsonValue

QUOTED_LINE_DISPLAY_LIMIT: Final[int] = 200

# Minimum length of a non-empty `evidence` substring that
# `_filter_general_comments` will accept. Short fragments (e.g. ``def``,
# ``self``) match too many lines in any non-trivial diff and give the
# model a free pass to invent findings; an 8-char floor forces the
# evidence to carry actual signal.
MIN_EVIDENCE_LEN: Final[int] = 8

# Phrases that mark a `must` general comment as a TEST COVERAGE GAP
# rather than a concrete defect. The system prompt classifies coverage
# gaps as `should`, but the model has empirically ignored that rule;
# this regex set is the runtime fallback that enforces it. Patterns
# match conservatively: each one targets the canonical phrasing of a
# coverage ask ("add a [...] test", "missing test for", "no test
# exercises", etc.) so a finding that merely *mentions* tests in
# passing is not downgraded.
# Token vocabulary the model uses to qualify "test" in coverage asks.
# Captured here as a single alternation so the per-pattern regex can
# treat any combination (including pairs like "serialization round-trip
# test") as a single noun phrase. Patterns are kept narrow -- e.g.
# `round[-\s]?trip` rather than just `round`, so unrelated bodies that
# happen to contain "Add a round of tests" do not match.
_TEST_QUALIFIER: Final[str] = (
    r"(?:"
    r"direct|focused|new|missing|"
    r"regression|completeness|forward[-\s]?compat\w*|"
    r"serialization|round[-\s]?trip|integration|e2e|"
    r"end[-\s]to[-\s]end|coverage|negative|edge[-\s]?case|"
    r"failure[-\s]?path|error[-\s]?path|happy[-\s]?path|smoke|"
    r"unit|functional"
    r")"
)

_TEST_COVERAGE_GAP_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        rf"\badd\s+(?:an?\s+)?(?:{_TEST_QUALIFIER}\s+){{0,3}}test\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bmissing\s+(?:a\s+)?test\b", re.IGNORECASE),
    re.compile(
        r"\bno\s+test\s+(?:exercises|covers|hits|asserts|guards)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\buntested\b", re.IGNORECASE),
    re.compile(
        r"\bnot\s+(?:directly\s+|currently\s+)?(?:exercised|covered|tested)\b",
        re.IGNORECASE,
    ),
)

# Phrases that ESCAPE the coverage-gap downgrade. If a `must` finding
# matches a coverage pattern AND any of these, we keep `must`: the
# author has tied the missing test to a real harm (security, data loss,
# production risk, race), which is the rare case where coverage is
# actually a blocker.
_REAL_RISK_TERMS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\b(?:security|auth(?:entication|orization)?|credential|secret|"
        r"token|password|injection|xss|csrf|ssrf|sandbox\s+escape|"
        r"privilege\s+escalation)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdata[-\s]?loss\b", re.IGNORECASE),
    re.compile(
        r"\bproduction[-\s]?(?:outage|incident|crash)\b", re.IGNORECASE
    ),
    re.compile(
        r"\brace\s+(?:condition|hazard)|deadlock|livelock\b", re.IGNORECASE
    ),
    re.compile(r"\bcorrupt(?:ion|ed?)\b", re.IGNORECASE),
)

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


def _filter_general_comments(
    review: JsonObject, diff_text: str
) -> tuple[JsonObject, list[JsonObject]]:
    """Drop ungrounded `general_comments` from a model review.

    Even with a strict JSON schema and an explicit anchor map, the model
    will sometimes emit `general_comments` that are not supported by
    anything in the diff -- for example, restating a checklist item from
    the system prompt without observing it on a changed line. Those
    findings are the most expensive class of false positive: they post a
    confident "must-fix" against code the patch did not change, and a
    human reviewer has to refute them.

    The runtime defends against that by re-checking each comment's
    `evidence` field against the actual diff text and discarding entries
    that fail. The drop policy is:

    * Malformed entry (severity / body / evidence not strings) -> drop.
    * `must` severity with empty `evidence` -> drop. A blocking finding
      that the model cannot even quote a diff line for is by definition
      unverifiable; we would rather lose a real signal than ship a
      confident hallucination.
    * Non-empty `evidence` shorter than :data:`MIN_EVIDENCE_LEN` after
      trimming -> drop (too generic to anchor a claim).
    * Non-empty `evidence` not present as a substring of `diff_text`
      after trimming -> drop.
    * Otherwise -> keep.

    Args:
        review: The validated model output (post
            :func:`_pr_review_llm._is_json_object` check). Mutated only
            via shallow copy; the caller's dict is left untouched.
        diff_text: The raw unified diff handed to the model. The same
            string the model saw -- if the diff was truncated before
            the call, evidence outside the truncated window is
            considered ungrounded (correctly: the model cannot quote
            something it did not receive).

    Returns:
        A two-tuple ``(filtered_review, dropped)`` where
        ``filtered_review`` is a shallow copy with `general_comments`
        replaced by the kept entries, and ``dropped`` is a list of
        :data:`JsonObject` carrying the original comment plus a
        ``_drop_reason`` key (``"must without evidence"``,
        ``"evidence too short"``, ``"evidence not in diff"``, or
        ``"malformed"``) so the orchestrator can emit one
        ``::warning::`` line per drop.
    """

    raw = review.get("general_comments")
    if not isinstance(raw, list):
        return review, []
    kept: list[JsonValue] = []
    dropped: list[JsonObject] = []
    for entry in raw:
        if not isinstance(entry, dict):
            dropped.append(
                {
                    "_drop_reason": "malformed",
                    "severity": "must",
                    "body": (
                        f"_(bot dropped a malformed general comment: "
                        f"expected object, got `{type(entry).__name__}`)_"
                    ),
                }
            )
            continue
        severity = entry.get("severity")
        body = entry.get("body")
        evidence = entry.get("evidence", "")
        if not (
            isinstance(severity, str)
            and isinstance(body, str)
            and isinstance(evidence, str)
        ):
            dropped.append(
                {
                    "_drop_reason": "malformed",
                    "severity": severity if isinstance(severity, str) else "must",
                    "body": str(body) if body is not None else "(no body)",
                }
            )
            continue
        evidence_norm = evidence.strip()
        if not evidence_norm:
            if severity == "must":
                dropped.append(
                    {
                        "_drop_reason": "must without evidence",
                        "severity": severity,
                        "body": body,
                    }
                )
                continue
            kept.append(entry)
            continue
        if len(evidence_norm) < MIN_EVIDENCE_LEN:
            dropped.append(
                {
                    "_drop_reason": "evidence too short",
                    "severity": severity,
                    "body": body,
                    "evidence": evidence,
                }
            )
            continue
        if evidence_norm not in diff_text:
            dropped.append(
                {
                    "_drop_reason": "evidence not in diff",
                    "severity": severity,
                    "body": body,
                    "evidence": evidence,
                }
            )
            continue
        kept.append(entry)
    new_review: JsonObject = dict(review)
    new_review["general_comments"] = kept
    return new_review, dropped


def _downgrade_coverage_musts(
    review: JsonObject,
) -> tuple[JsonObject, list[JsonObject]]:
    """Reclassify `must` general comments that are pure test-coverage asks.

    The system prompt is explicit that test-coverage gaps are at most
    `should`, but the LLM has empirically continued to emit them as
    `must` (PR #81's `c733b5d` review re-flagged "add a serialization
    round-trip test" as `must` even after the prompt update). Severity
    inflation matters because `must` flips the Reviews API ``event`` to
    ``REQUEST_CHANGES``, which on a branch-protected repo can block
    merge -- a coverage suggestion should not.

    A finding is downgraded from `must` to `should` when ALL of:

    * `severity == "must"` and `body` is a string.
    * `body` matches at least one phrase in
      :data:`_TEST_COVERAGE_GAP_PATTERNS` (an explicit "add a [...] test",
      "missing test", "no test exercises", "untested", etc.).
    * `body` does NOT match any pattern in :data:`_REAL_RISK_TERMS`
      (security, data loss, production outage, race, corruption). When
      coverage is tied to a real harm, `must` survives.

    Inline comments are NOT downgraded: inline anchors are tied to a
    specific line, which usually indicates a concrete defect rather
    than a coverage opinion. Coverage asks naturally fall in
    `general_comments` (no specific line to anchor to).

    Args:
        review: The model output, post-grounding-filter.

    Returns:
        ``(filtered_review, downgraded)`` where ``filtered_review`` is
        a shallow copy with the offending entries' ``severity`` set to
        ``"should"`` and a ``_downgraded_from`` audit field added so a
        future test or operator log can spot the rewrite, and
        ``downgraded`` is the list of original entries (with
        ``_downgraded_from``) for ``::warning::`` emission.
    """

    raw = review.get("general_comments")
    if not isinstance(raw, list):
        return review, []
    new_items: list[JsonValue] = []
    downgraded: list[JsonObject] = []
    for entry in raw:
        if not isinstance(entry, dict):
            new_items.append(entry)
            continue
        if entry.get("severity") != "must":
            new_items.append(entry)
            continue
        body = entry.get("body")
        if not isinstance(body, str):
            new_items.append(entry)
            continue
        if not any(p.search(body) for p in _TEST_COVERAGE_GAP_PATTERNS):
            new_items.append(entry)
            continue
        if any(p.search(body) for p in _REAL_RISK_TERMS):
            new_items.append(entry)
            continue
        rewritten: JsonObject = dict(entry)
        rewritten["severity"] = "should"
        rewritten["_downgraded_from"] = "must"
        new_items.append(rewritten)
        downgraded.append(rewritten)
    new_review: JsonObject = dict(review)
    new_review["general_comments"] = new_items
    return new_review, downgraded


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
    review: JsonObject,
    demoted: list[JsonObject],
    head_sha_marker: str,
    *,
    truncation_notice: str | None = None,
) -> str:
    """Assemble the markdown summary body posted to the Reviews API.

    Layout (sections joined with a blank line):

    1. Optional ``truncation_notice`` italic line, when the diff was
       cropped before being sent to the model. Surfacing this lets a
       human reviewer know up-front that the bot may have missed
       context outside the truncation window.
    2. ``**Verdict:** \\`<verdict>\\``` -- the model's top-level
       verdict, or ``comment`` if the field is missing or non-string.
    3. The ``summary`` paragraph from the model, when present.
    4. One severity-grouped bullet section per severity in
       :data:`_SEVERITY_ORDER` (only severities with at least one
       item are emitted; see :func:`_format_general_section`).
       Demoted inline comments are appended to the model's general
       comments before grouping so misaligned anchors still surface.
    5. The dedup marker passed in by the caller. The marker MUST be
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
        truncation_notice: Optional sentence describing how the diff
            was cropped before the LLM call (e.g.
            ``"_Diff truncated to 120000 chars; findings outside that
            window are not present._"``). Rendered verbatim as the
            first section when supplied.

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

    sections: list[str] = []
    if truncation_notice:
        sections.append(truncation_notice)
    sections.append(f"**Verdict:** `{verdict_text}`")
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
