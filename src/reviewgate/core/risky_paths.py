"""Risky-path-without-rationale heuristic (docs/DESIGN.md \u00a710.10 + \u00a712).

Combines three pieces of the spec:

* \u00a710.6 risky-path patterns (the ``risky`` boolean on each
  :class:`reviewgate.core.schemas.FileCategoryRow`, populated by the
  categoriser per #9).
* \u00a710.10 "missing rationale for risky paths" rule -- the PR body
  should explain *why* a risky path is touched.
* \u00a712 ``policy.fail_on_risky_paths_without_context`` (default
  ``True``) -- toggles the warning severity between ``"high"`` (fail
  tier) and ``"medium"`` (warn tier).

The heuristic stays deterministic and conservative:

1. Collect the set of changed files marked ``risky=True``.
2. Filter to files that carry at least one of the four \u00a710.6
   "named" risk categories (``auth``, ``billing``, ``infra``,
   ``migration``). A file that is risky purely because the user added
   it to a custom ``risky_paths`` list does not get checked here -- we
   would not have a keyword set to match against and false negatives
   are preferable to noisy false positives.
3. Check whether the PR body mentions rationale via either:

   * a keyword from the touched risky categories' synonym sets
     (``auth`` -> ``{auth, authentication, login, session, ...}``,
     etc.), OR
   * the basename or any path segment of any risky file (so a body
     that says ``rewrites services/auth/login.py`` is treated as
     explaining the change).

4. If no rationale is detected, emit a single \u00a710.12 warning whose
   evidence enumerates the risky filenames and the categories that
   triggered the check.

Pure: no I/O, no GitHub or LLM dependency.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

from .schemas import EngineWarning, FileCategory, FileCategoryRow, WarningSeverity

WARN_CODE_RISKY_NO_RATIONALE: Final[str] = "risky_paths_without_rationale"
"""Stable \u00a710.12 code emitted by :func:`risky_paths_warning`."""

# The four \u00a710.5 categories that the categoriser derives from the
# \u00a710.6 risky-path subsets. We only check rationale for files in
# these categories so the heuristic can ground its decision on a known
# keyword set.
_RISK_CATEGORIES: Final[frozenset[FileCategory]] = frozenset(
    {"auth", "billing", "infra", "migration"},
)

# Keyword sets the body is searched against, per touched risky
# category. Synonyms are lowercased; the search itself is
# case-insensitive. The lists are intentionally narrow -- we want a
# match to feel like a real explanation, not a coincidence.
_CATEGORY_KEYWORDS: Final[dict[FileCategory, frozenset[str]]] = {
    "auth": frozenset(
        {
            "auth",
            "authentication",
            "authorization",
            "login",
            "logout",
            "session",
            "permission",
            "permissions",
            "rbac",
            "sso",
            "oauth",
            "token",
            "credential",
            "credentials",
            "mfa",
        },
    ),
    "billing": frozenset(
        {
            "billing",
            "payment",
            "payments",
            "invoice",
            "invoices",
            "charge",
            "charges",
            "subscription",
            "subscriptions",
            "refund",
            "refunds",
            "stripe",
            "money",
            "checkout",
        },
    ),
    "infra": frozenset(
        {
            "infra",
            "infrastructure",
            "deploy",
            "deployment",
            "docker",
            "compose",
            "kubernetes",
            "k8s",
            "terraform",
            "ci",
            "cd",
            "workflow",
            "workflows",
            "pipeline",
            "release",
            "rollout",
            "helm",
        },
    ),
    "migration": frozenset(
        {
            "migration",
            "migrations",
            "schema",
            "ddl",
            "alter",
            "rollback",
            "downgrade",
            "backfill",
            "migrate",
            "column",
            "columns",
            "table",
            "tables",
            "index",
            "indexes",
        },
    ),
}

_SEVERITY_FAIL: Final[WarningSeverity] = "high"
_SEVERITY_WARN: Final[WarningSeverity] = "medium"

# Word-boundary helper. Splits the body into a lowercase set of
# alphanumeric tokens once so keyword / path-segment lookups are O(1).
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    """Return a lowercase token set extracted from ``text``.

    Splits on any non-word character; punctuation, paths, and Markdown
    formatting all dissolve into bare tokens (``services/auth/login.py``
    -> ``{services, auth, login, py}``). Built on the stdlib :mod:`re`
    so the engine stays pure.
    """

    return {match.group(0).lower() for match in _TOKEN_RE.finditer(text)}


def _file_path_tokens(filename: str) -> set[str]:
    """Tokens from a file's basename and every directory segment.

    A risky file at ``services/auth/login.py`` contributes
    ``{services, auth, login, py}``. Whether *any* of these appear in
    the PR body is enough to count as a mention of that specific file.
    """

    return _tokenize(filename)


def _categories_present(rows: Iterable[FileCategoryRow]) -> set[FileCategory]:
    """Union of the four risk categories carried by the given rows."""

    seen: set[FileCategory] = set()
    for row in rows:
        for cat in row.categories:
            if cat in _RISK_CATEGORIES:
                seen.add(cat)
    return seen


def risky_paths_warning(
    file_categories: Iterable[FileCategoryRow],
    body: str,
    *,
    fail_on_risky_paths_without_context: bool,
) -> EngineWarning | None:
    """Emit the \u00a710.10 + \u00a712 warning, or ``None`` if context is sufficient.

    Args:
        file_categories: Categoriser output (#9). Only rows with
            ``risky=True`` and at least one of :data:`_RISK_CATEGORIES`
            are considered.
        body: PR body from :class:`reviewgate.core.schemas.PRRecord`.
        fail_on_risky_paths_without_context: \u00a712 policy toggle. When
            ``True`` (the spec default) the warning lands at
            ``severity="high"``, contributing a "fail" data point to
            the \u00a710.13 baseline aggregator. When ``False`` the warning
            lands at ``severity="medium"``.

    Returns:
        ``None`` when no risky files are touched, when no risky file
        carries a known risk category, or when the body mentions
        rationale (a category synonym OR any path segment of a risky
        file). Otherwise a single warning whose evidence lists the
        risky filenames and the categories that fired.
    """

    rows = list(file_categories)
    risky_rows = [r for r in rows if r.risky]
    if not risky_rows:
        return None

    risky_categories = _categories_present(risky_rows)
    if not risky_categories:
        # Risky by user-pattern only (no \u00a710.5 mapping); we have no
        # keyword set to ground the rationale check on. Skip silently.
        return None

    body_tokens = _tokenize(body)

    keyword_hit = any(
        body_tokens & _CATEGORY_KEYWORDS[cat] for cat in risky_categories
    )
    if keyword_hit:
        return None

    path_hit = any(body_tokens & _file_path_tokens(r.filename) for r in risky_rows)
    if path_hit:
        return None

    severity = (
        _SEVERITY_FAIL if fail_on_risky_paths_without_context else _SEVERITY_WARN
    )
    risky_filenames = [r.filename for r in risky_rows]
    return EngineWarning(
        code=WARN_CODE_RISKY_NO_RATIONALE,
        severity=severity,
        message=(
            "PR touches risky files but the body does not mention the "
            "affected areas (e.g. " + ", ".join(sorted(risky_categories))
            + "). Explain the change so reviewers know what to focus on."
        ),
        evidence={
            "risky_files": risky_filenames,
            "risky_categories": sorted(risky_categories),
            "policy": "fail_on_risky_paths_without_context",
        },
    )


__all__ = [
    "WARN_CODE_RISKY_NO_RATIONALE",
    "risky_paths_warning",
]
