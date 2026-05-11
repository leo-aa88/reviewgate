"""PR author classification and dependency automation overrides (DESIGN §10.4).

This module does two things:

1. **Author kind** (§10.4.2) — classifies ``EngineInput.pr.author`` (GitHub
   ``user.login``) into a small closed set: human collaborator, dependency
   automation (Dependabot / Renovate), coding-agent integration accounts
   (Copilot, Cursor, Codex connector, …), or other ``[bot]`` identities.
   This reflects **who opened the PR on GitHub**, not how individual lines
   were produced, and must not be read as code provenance.

2. **Manifest-only dependency PRs** (§10.4.1) — when the author is a known
   dependency bot and every changed file is manifest/lockfile-only (no
   ``source`` rows), clamps ``human_loc_changed`` so §10.3 size warnings do
   not fire on manifest churn.

Shipped login lists are intentionally explicit (frozensets); extend them
via project issues when new stable ``user.login`` values are confirmed.

Pure: no I/O, no GitHub API dependency.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import JsonValue

from .schemas import FileCategoryRow
from .size import SizeStats

PrAuthorKind = Literal[
    "human",
    "dependency_automation",
    "coding_agent_automation",
    "generic_automation",
]
"""Closed set stored in §10.2 ``stats["pr_author_kind"]``."""

KNOWN_DEPENDENCY_AUTOMATION_LOGINS: Final[frozenset[str]] = frozenset(
    {
        "dependabot[bot]",
        "renovate[bot]",
        "renovate-bot",
    },
)
"""GitHub ``user.login`` values treated as dependency workflow bots."""

KNOWN_CODING_AGENT_AUTOMATION_LOGINS: Final[frozenset[str]] = frozenset(
    {
        # GitHub Copilot (workspace / agent flows; login casing varies by product surface).
        "Copilot",
        "copilot[bot]",
        "github-copilot[bot]",
        "copilot-swe-agent[bot]",
        # Cursor agent / GitHub App style identities (extend as confirmed).
        "cursor[bot]",
        "cursor-agent[bot]",
        # OpenAI Codex GitHub connector (common App login).
        "chatgpt-codex-connector[bot]",
        # Anthropic Claude Code / similar GitHub App publishers (extend as confirmed).
        "claude[bot]",
        "anthropic-claude[bot]",
        # Cognition Devin integration (when it opens PRs as the App bot).
        "devin-ai-integration[bot]",
    },
)
"""``user.login`` values for coding-agent or AI-coding integrations that open PRs.

Notes:
    GitHub returns ``user.login`` case-sensitively in JSON; unknown variants
    fall through to :func:`classify_pr_author_login` until added here.
"""

_MANIFEST_CATEGORIES_FOR_OVERRIDE: Final[frozenset[str]] = frozenset(
    {"dependency", "lockfile"},
)

_DEP_LOGIN_LOWER: Final[frozenset[str]] = frozenset(
    login.lower() for login in KNOWN_DEPENDENCY_AUTOMATION_LOGINS
)
_AGENT_LOGIN_LOWER: Final[frozenset[str]] = frozenset(
    login.lower() for login in KNOWN_CODING_AGENT_AUTOMATION_LOGINS
)


def classify_pr_author_login(login: str) -> PrAuthorKind:
    """Map ``user.login`` to a :data:`PrAuthorKind` label.

    Resolution order: dependency bots, coding-agent bots, generic
    ``[bot]`` accounts, then human. Empty or whitespace-only logins are
    ``human`` (unknown opener).

    Args:
        login: ``EngineInput.pr.author`` from the §10.1 envelope.

    Returns:
        One of :data:`PrAuthorKind`.

    Examples:
        >>> classify_pr_author_login("dependabot[bot]")
        'dependency_automation'
        >>> classify_pr_author_login("Copilot")
        'coding_agent_automation'
        >>> classify_pr_author_login("github-actions[bot]")
        'generic_automation'
        >>> classify_pr_author_login("octocat")
        'human'
    """

    stripped = login.strip()
    if not stripped:
        return "human"
    key = stripped.lower()
    if key in _DEP_LOGIN_LOWER:
        return "dependency_automation"
    if key in _AGENT_LOGIN_LOWER:
        return "coding_agent_automation"
    if stripped.endswith("[bot]"):
        return "generic_automation"
    return "human"


def is_known_dependency_automation_login(login: str) -> bool:
    """Return whether ``login`` is a configured dependency automation bot.

    Args:
        login: ``EngineInput.pr.author`` (GitHub ``user.login``).

    Returns:
        ``True`` when :func:`classify_pr_author_login` would return
        ``\"dependency_automation\"``.
    """

    return classify_pr_author_login(login) == "dependency_automation"


def is_manifest_only_dependency_automation_pr(
    author: str,
    file_categories: list[FileCategoryRow],
) -> bool:
    """Return whether size stats should treat this PR as manifest-only bot work.

    A PR qualifies when the author is a known dependency bot, every row
    touches a dependency manifest or lockfile, and no row is categorized
    as ``source`` (so mixed human + bot dependency PRs keep normal stats).

    Args:
        author: ``EngineInput.pr.author``.
        file_categories: Categorizer output for active files.

    Returns:
        ``True`` when the override rules apply; ``False`` otherwise.
    """

    if not is_known_dependency_automation_login(author):
        return False
    if not file_categories:
        return False
    for row in file_categories:
        if "source" in row.categories:
            return False
        cats = frozenset(row.categories)
        if not (cats & _MANIFEST_CATEGORIES_FOR_OVERRIDE):
            return False
    return True


def finalize_size_stats_for_pr_author(
    base: SizeStats,
    *,
    author: str,
    file_categories: list[FileCategoryRow],
) -> tuple[SizeStats, dict[str, JsonValue]]:
    """Adjust :class:`SizeStats` for dependency bots and attach author metadata.

    Always injects ``pr_author_kind`` (and ``pr_author_login`` when non-empty)
    for §10.2 consumers. When :func:`is_manifest_only_dependency_automation_pr`
    is true, sets ``human_loc_changed`` to ``0`` and ``excluded_loc_changed`` to
    ``raw_loc_changed`` so §10.3 size warnings do not fire on manifest churn.

    Args:
        base: Output of :func:`reviewgate.core.size.compute_size_stats`.
        author: ``EngineInput.pr.author``.
        file_categories: Same rows passed into ``compute_size_stats``.

    Returns:
        ``(stats, extra_stats)`` to merge into the report ``stats`` map.

    Example:
        ``dependabot[bot]`` with only ``requirements.txt`` yields
        ``pr_author_kind == \"dependency_automation\"`` and
        ``dependency_automation_manifest_only: true``.
    """

    extra: dict[str, JsonValue] = {}
    stripped = author.strip()
    extra["pr_author_kind"] = classify_pr_author_login(author)
    if stripped:
        extra["pr_author_login"] = stripped

    if not is_manifest_only_dependency_automation_pr(author, file_categories):
        return base, extra

    adjusted = base.model_copy(
        update={
            "human_loc_changed": 0,
            "excluded_loc_changed": base.raw_loc_changed,
        },
    )
    extra["dependency_automation_manifest_only"] = True
    return adjusted, extra


__all__ = [
    "KNOWN_CODING_AGENT_AUTOMATION_LOGINS",
    "KNOWN_DEPENDENCY_AUTOMATION_LOGINS",
    "PrAuthorKind",
    "classify_pr_author_login",
    "finalize_size_stats_for_pr_author",
    "is_known_dependency_automation_login",
    "is_manifest_only_dependency_automation_pr",
]
