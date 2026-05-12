"""Markdown summary rendering for ReviewGate Action workflow output (DESIGN §13 / §14).

Maps a :class:`reviewgate.core.schemas.ReviewabilityReport` to the human-readable
block written to stderr and ``$GITHUB_STEP_SUMMARY``. Keeps ``run_core`` focused on
CLI orchestration and stays free of engine imports beyond schemas.

This module owns only presentation strings and layout; verdict logic lives in
``reviewgate.core``.
"""

from __future__ import annotations

from typing import Final

from reviewgate.core.schemas import Reviewability, ReviewabilityReport

VERDICT_GLYPH: Final[dict[Reviewability, str]] = {
    "PASS": "[PASS]",
    "WARN": "[WARN]",
    "FAIL": "[FAIL]",
}

# Markdown bullet label for ``stats["human_loc_changed"]`` (DESIGN §10.4).
STAT_LABEL_HUMAN_LOC: Final[str] = (
    "LOC after §10.4 exclusions (`human_loc_changed`)"
)

# Short blurbs for ``stats["pr_author_kind"]`` (DESIGN §10.4.2); keys match engine output.
PR_AUTHOR_KIND_LABELS: Final[dict[str, str]] = {
    "human": "human collaborator account",
    "dependency_automation": "dependency automation (Dependabot / Renovate)",
    "coding_agent_automation": "coding-agent integration account",
    "generic_automation": "other GitHub App / bot account (`[bot]` suffix)",
}


def render_summary(report: ReviewabilityReport) -> str:
    """Render a Markdown-flavoured human summary of ``report``.

    The output is consumed by:

    * the workflow log (stderr); and
    * ``$GITHUB_STEP_SUMMARY``, where Markdown renders into the job Summary panel.

    Note:
        Uses :data:`STAT_LABEL_HUMAN_LOC` so the workflow log matches DESIGN §10.4
        semantics while stdout JSON keeps the stable ``human_loc_changed`` field name.

    Args:
        report: §10.2 deterministic report from :func:`reviewgate.core.engine.analyze`.

    Returns:
        A trailing-newline-terminated Markdown string.
    """

    lines: list[str] = []
    glyph = VERDICT_GLYPH[report.reviewability]
    lines.append(f"## ReviewGate {glyph} `{report.reviewability}`")
    lines.append("")

    stats = report.stats
    files_changed = stats.get("files_changed")
    raw_loc = stats.get("raw_loc_changed")
    human_loc = stats.get("human_loc_changed")
    author_kind = stats.get("pr_author_kind")
    show_numeric_stats = any(v is not None for v in (files_changed, raw_loc, human_loc))
    show_author_stats = isinstance(author_kind, str)
    if show_numeric_stats or show_author_stats:
        lines.append("**Stats**")
        lines.append("")
        if show_numeric_stats:
            lines.append(f"- Files changed: `{files_changed}`")
            lines.append(f"- Raw LOC changed: `{raw_loc}`")
            lines.append(f"- {STAT_LABEL_HUMAN_LOC}: `{human_loc}`")
        if show_author_stats:
            blurb = PR_AUTHOR_KIND_LABELS.get(author_kind, author_kind)
            login = stats.get("pr_author_login")
            login_suffix = (
                f" — login `{login}`" if isinstance(login, str) and login.strip() else ""
            )
            lines.append(
                f"- PR author class: `{author_kind}` ({blurb}){login_suffix} (§10.4.2)."
            )
        if stats.get("dependency_automation_manifest_only") is True:
            lines.append(
                "- Manifest-only dependency automation: "
                "``human_loc_changed`` clamped to ``0`` for §10.3 thresholds."
            )
        lines.append("")

    if report.warnings:
        lines.append(f"**Warnings ({len(report.warnings)})**")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- `{warning.severity}` `{warning.code}` -- {warning.message}")
        lines.append("")
    else:
        lines.append("No deterministic warnings fired.")
        lines.append("")

    if report.suggested_labels:
        joined = ", ".join(f"`{label}`" for label in report.suggested_labels)
        lines.append(f"**Suggested labels:** {joined}")
        lines.append("")

    if report.file_categories:
        risky = sum(1 for row in report.file_categories if row.risky)
        lines.append(f"**File categories:** {len(report.file_categories)} files ({risky} risky)")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "PR_AUTHOR_KIND_LABELS",
    "STAT_LABEL_HUMAN_LOC",
    "VERDICT_GLYPH",
    "render_summary",
]
