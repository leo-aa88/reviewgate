"""Linked-issue / ticket detection (docs/DESIGN.md \u00a710.10).

Implements the "missing linked issue" deterministic check: scans the
PR title and body for any of the reference forms enumerated in
\u00a710.10 and, when none is found AND policy ``require_linked_issue`` is
enabled, emits a single :class:`reviewgate.core.schemas.EngineWarning`
(``code="missing_linked_issue"``, ``severity="medium"``).

Detected forms (\u00a710.10 verbatim):

* ``#123`` -- bare GitHub-style numeric reference.
* ``org/repo#123`` -- cross-repo numeric reference (alias of the bare
  form, scoped to a different repo).
* ``GH-123`` -- GitHub legacy-style prefixed reference.
* External IDs in the ``ABC-123`` shape (Jira / Linear / Shortcut /
  YouTrack), required to start with at least two uppercase letters so
  random capitalised words such as ``A-1`` do not trip the matcher.
* Jira issue URLs: ``https://*.atlassian.net/browse/ABC-123``.
* Linear issue URLs: ``https://linear.app/<team>/issue/ABC-123``.
* GitHub issue / PR URLs:
  ``https://github.com/<owner>/<repo>/(issues|pull)/<number>``.

Closing keywords (``fixes #123``, ``closes #123``, ``resolves #123``)
are intentionally **not** matched as a separate pattern -- the
trailing ``#123`` already matches the bare-numeric rule, so an extra
keyword regex would only add false-negative surface area without
catching anything new.

Pure: no I/O, no GitHub or LLM dependency. Patterns are precompiled at
import time.
"""

from __future__ import annotations

import re
from typing import Final

from .schemas import EngineWarning, WarningSeverity

WARN_CODE_MISSING_LINKED_ISSUE: Final[str] = "missing_linked_issue"
"""Stable \u00a710.12 code emitted by :func:`linked_issue_warning`."""

_SEVERITY: Final[WarningSeverity] = "medium"


# --- compiled detection patterns ------------------------------------------

# `#123` and `org/repo#123` (the latter must come first so a bare `#123`
# inside `owner/repo#123` is recognised as the cross-repo form, not as
# a separate hit).
_GITHUB_NUMERIC_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:(?<=^)|(?<=[\s(\[{,]))"  # left boundary: start, whitespace, or punctuation
    r"(?:[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)?#\d+"
    r"\b",
)

# `GH-123` legacy form (GitHub historically used this in commit
# messages and changelogs; still common in some workflows).
_GH_PREFIXED_RE: Final[re.Pattern[str]] = re.compile(
    r"\bGH-\d+\b",
    flags=re.IGNORECASE,
)

# `ABC-123` style external IDs. The minimum two-uppercase-letter prefix
# avoids noise from product-name patterns like `i-007` or `T-1000`. The
# project key is uppercase letters and digits (Jira allows digits after
# the first letter); the issue number is one or more digits.
_EXTERNAL_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Z][A-Z0-9]+-\d+\b",
)

# Provider-specific URLs. We only need the URL prefix to match; we do
# not parse out the trailing ID since presence of the URL is enough to
# satisfy \u00a710.10.
_JIRA_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"https?://[\w.-]+\.atlassian\.net/browse/[A-Z][A-Z0-9]+-\d+",
    flags=re.IGNORECASE,
)
_LINEAR_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"https?://linear\.app/[\w.-]+/issue/[A-Z][A-Z0-9]+-\d+",
    flags=re.IGNORECASE,
)
_GITHUB_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"https?://github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+",
    flags=re.IGNORECASE,
)

_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    _JIRA_URL_RE,
    _LINEAR_URL_RE,
    _GITHUB_URL_RE,
    _GH_PREFIXED_RE,
    _EXTERNAL_ID_RE,
    _GITHUB_NUMERIC_RE,
)


def find_issue_references(title: str, body: str) -> list[str]:
    """Return all \u00a710.10 issue references found in ``title`` and ``body``.

    Order matches the \u00a710.10 list (URLs first, then prefixed / external
    IDs, then bare numeric forms) so the most specific match wins for
    diagnostic purposes; deduplication preserves first occurrence.

    Args:
        title: PR title from :class:`reviewgate.core.schemas.PRRecord`.
        body: PR body from :class:`reviewgate.core.schemas.PRRecord`.

    Returns:
        A deduplicated list of matched substrings in the order they
        were first seen across the patterns. The empty list signals
        "no linked issue found".
    """

    haystack = f"{title}\n{body}"
    seen: list[str] = []
    seen_set: set[str] = set()
    for pattern in _PATTERNS:
        for match in pattern.findall(haystack):
            if match not in seen_set:
                seen_set.add(match)
                seen.append(match)
    return seen


def linked_issue_warning(
    title: str,
    body: str,
    *,
    require_linked_issue: bool,
) -> EngineWarning | None:
    """Emit a \u00a710.10 missing-linked-issue warning when policy demands one.

    Args:
        title: PR title from :class:`PRRecord`.
        body: PR body from :class:`PRRecord`.
        require_linked_issue: ``policy.require_linked_issue`` from the
            effective :class:`reviewgate.core.config.ReviewGateConfig`
            (defaults to ``True`` per \u00a712).

    Returns:
        ``None`` when ``require_linked_issue`` is false, OR when at
        least one \u00a710.10 reference is found in title / body. Otherwise
        a single ``severity="medium"`` warning with evidence:

        * ``policy``: ``"require_linked_issue"`` (records which policy
          enabled the check).
        * ``patterns_checked``: the count of distinct \u00a710.10 patterns
          considered, so reviewers can verify nothing was skipped.
    """

    if not require_linked_issue:
        return None
    if find_issue_references(title, body):
        return None
    return EngineWarning(
        code=WARN_CODE_MISSING_LINKED_ISSUE,
        severity=_SEVERITY,
        message=(
            "PR title and body contain no recognized issue or ticket "
            "reference (e.g. #123, GH-123, ABC-123, or a Jira / Linear "
            "/ GitHub issue URL)."
        ),
        evidence={
            "policy": "require_linked_issue",
            "patterns_checked": len(_PATTERNS),
        },
    )


__all__ = [
    "WARN_CODE_MISSING_LINKED_ISSUE",
    "find_issue_references",
    "linked_issue_warning",
]
