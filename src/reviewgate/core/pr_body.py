"""Weak PR body heuristic (docs/DESIGN.md \u00a710.10).

Emits a single \u00a710.12 warning (``severity="medium"``, code
``"weak_pr_body"``) when the normalised PR body is empty, whitespace
only, or contains fewer than :data:`MIN_MEANINGFUL_CHARS` meaningful
characters once template noise has been stripped.

"Meaningful" is defined as text that survives the following cleaning
pipeline (see :func:`meaningful_text`):

1. HTML comments ``<!-- ... -->`` (multi-line) are removed -- PR
   templates use them for hidden author guidance and they should never
   count toward content length.
2. Markdown horizontal rules (``---``, ``***``, ``___``) are removed --
   they are pure structure.
3. Markdown heading prefixes (``#``, ``##`` ...) are removed but their
   text content is kept -- a heading like ``## Why`` is still
   informative, just not a substitute for body content.
4. Per-line list and quote markers are stripped: ``-``, ``*``, ``+``,
   ``>``, numbered list prefixes (``1.``), and unchecked / checked
   checkbox markers (``[ ]``, ``[x]``).
5. The remaining characters are concatenated and whitespace is
   normalised; the count of non-whitespace characters in that result
   is compared against :data:`MIN_MEANINGFUL_CHARS` (80, per the spec).

The ``evidence.reason`` field carries either ``"empty"`` or
``"insufficient_content"`` so downstream label rules and report
formatters can word the message appropriately.

Pure: no I/O, no GitHub or LLM dependency. The cleaning is implemented
with :mod:`re` from the stdlib (allowed by the \u00a74.1 purity guard).
"""

from __future__ import annotations

import re
from typing import Final

from .schemas import EngineWarning, WarningSeverity

# Threshold and stable warning code (\u00a710.10 / \u00a710.12)

MIN_MEANINGFUL_CHARS: Final[int] = 80
"""Minimum non-whitespace character count after template-noise removal.

Sourced verbatim from \u00a710.10 ("fewer than 80 meaningful characters").
"""

WARN_CODE_WEAK_BODY: Final[str] = "weak_pr_body"
"""Stable \u00a710.12 warning code emitted by :func:`weak_body_warning`."""

REASON_EMPTY: Final[str] = "empty"
"""``evidence.reason`` value when the body is empty or whitespace-only."""

REASON_INSUFFICIENT: Final[str] = "insufficient_content"
"""``evidence.reason`` value when meaningful chars < :data:`MIN_MEANINGFUL_CHARS`."""

_SEVERITY: Final[WarningSeverity] = "medium"


# --- compiled cleaning patterns --------------------------------------------
#
# Compiled at import time so the heuristic stays cheap when called for
# every PR. Patterns are intentionally conservative -- the goal is to
# strip *obvious* template scaffolding, not to reformat user prose.

_HTML_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"<!--.*?-->",
    flags=re.DOTALL,
)
_HORIZONTAL_RULE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*([-*_])(?:\s*\1){2,}\s*$",
    flags=re.MULTILINE,
)
_HEADING_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*#{1,6}\s+",
    flags=re.MULTILINE,
)
_BLOCKQUOTE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*>+\s?",
    flags=re.MULTILINE,
)
_BULLET_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*([-*+]|\d+\.)\s+",
    flags=re.MULTILINE,
)
_CHECKBOX_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*\[[ xX]\]\s*",
    flags=re.MULTILINE,
)
_WHITESPACE_RUN_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


def meaningful_text(body: str) -> str:
    """Strip template / markdown noise from ``body`` and return the residue.

    The result preserves the substantive prose -- heading text, list
    items, paragraphs, code -- but drops the structural scaffolding
    that PR templates contribute. Whitespace is collapsed to single
    spaces so callers can compare lengths consistently.

    The function is the single source of truth for "meaningful" in the
    \u00a710.10 sense; tests lock the cleaning pipeline against canonical
    inputs in :mod:`tests.test_pr_body`.
    """

    cleaned = _HTML_COMMENT_RE.sub("", body)
    # Order matters: horizontal rules must be removed before heading /
    # bullet stripping, otherwise ``---`` could be mis-identified as a
    # bullet on a future regex change.
    cleaned = _HORIZONTAL_RULE_RE.sub("", cleaned)
    # Bullet/checkbox markers can sit on the same line, e.g. ``- [ ] todo``.
    # Strip the bullet first so the checkbox regex sees the bracket at
    # the start of the line.
    cleaned = _BULLET_PREFIX_RE.sub("", cleaned)
    cleaned = _CHECKBOX_PREFIX_RE.sub("", cleaned)
    cleaned = _BLOCKQUOTE_PREFIX_RE.sub("", cleaned)
    cleaned = _HEADING_PREFIX_RE.sub("", cleaned)
    return _WHITESPACE_RUN_RE.sub(" ", cleaned).strip()


def meaningful_char_count(body: str) -> int:
    """Number of non-whitespace characters in :func:`meaningful_text`.

    Whitespace inside :func:`meaningful_text` has already been
    normalised, so a final ``replace`` of single spaces is enough.
    """

    return len(meaningful_text(body).replace(" ", ""))


def weak_body_warning(body: str) -> EngineWarning | None:
    """Return a \u00a710.10 warning for ``body``, or ``None`` if the body is fine.

    Args:
        body: The PR body string from
            :class:`reviewgate.core.schemas.PRRecord`. May be empty.

    Returns:
        ``None`` when the body has at least :data:`MIN_MEANINGFUL_CHARS`
        meaningful characters. Otherwise an
        :class:`reviewgate.core.schemas.EngineWarning` with code
        :data:`WARN_CODE_WEAK_BODY`, ``severity="medium"``, and
        ``evidence`` carrying:

        * ``reason``: :data:`REASON_EMPTY` or :data:`REASON_INSUFFICIENT`.
        * ``meaningful_chars``: the integer count produced by
          :func:`meaningful_char_count`.
        * ``threshold``: :data:`MIN_MEANINGFUL_CHARS`, included so
          reviewers can reproduce the decision.
    """

    cleaned = meaningful_text(body)
    if not cleaned:
        # Catches both the literal empty string and bodies whose only
        # content is template scaffolding (HTML comments, horizontal
        # rules) -- once :func:`meaningful_text` returns ``""`` there is
        # nothing left for a reviewer to read.
        return EngineWarning(
            code=WARN_CODE_WEAK_BODY,
            severity=_SEVERITY,
            message=(
                "PR body is empty or contains only template scaffolding; "
                "reviewers need at least a sentence describing the change."
            ),
            evidence={
                "reason": REASON_EMPTY,
                "meaningful_chars": 0,
                "threshold": MIN_MEANINGFUL_CHARS,
            },
        )

    count = len(cleaned.replace(" ", ""))
    if count < MIN_MEANINGFUL_CHARS:
        return EngineWarning(
            code=WARN_CODE_WEAK_BODY,
            severity=_SEVERITY,
            message=(
                f"PR body has only {count} meaningful characters after "
                f"stripping template scaffolding (threshold "
                f"{MIN_MEANINGFUL_CHARS})."
            ),
            evidence={
                "reason": REASON_INSUFFICIENT,
                "meaningful_chars": count,
                "threshold": MIN_MEANINGFUL_CHARS,
            },
        )
    return None


__all__ = [
    "MIN_MEANINGFUL_CHARS",
    "REASON_EMPTY",
    "REASON_INSUFFICIENT",
    "WARN_CODE_WEAK_BODY",
    "meaningful_char_count",
    "meaningful_text",
    "weak_body_warning",
]
