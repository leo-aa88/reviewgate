"""Merge LLM narrative into :class:`~reviewgate.core.schemas.ReviewabilityReport` (§11.8)."""

from __future__ import annotations

from decimal import Decimal

from reviewgate.core.config import Labels
from reviewgate.core.report import suggested_labels
from reviewgate.core.schemas import ReviewabilityReport, SplitHint

from reviewgate.app.llm.schemas import LlmReviewabilityReport
from reviewgate.app.llm.verdict import merge_final_reviewability


def apply_llm_to_deterministic_report(
    deterministic: ReviewabilityReport,
    llm: LlmReviewabilityReport | None,
    *,
    labels: Labels,
) -> ReviewabilityReport:
    """§11.8 merge: escalate reviewability; enrich checklist/splits; stash LLM stats."""

    if llm is None:
        return deterministic

    final_rev = merge_final_reviewability(deterministic.reviewability, llm.reviewability)
    split_hints = (
        [SplitHint(title=s.title, scope=s.scope) for s in llm.split_suggestions]
        if llm.split_suggestions
        else list(deterministic.split_hints)
    )
    checklist = (
        [str(x) for x in llm.reviewer_checklist if str(x).strip()]
        if llm.reviewer_checklist
        else list(deterministic.reviewer_checklist)
    )
    stats: dict[str, object] = {**deterministic.stats}
    stats["llm"] = {
        "summary": llm.summary,
        "issues": [i.model_dump(mode="json") for i in llm.issues],
    }
    return ReviewabilityReport(
        reviewability=final_rev,
        stats=stats,
        warnings=deterministic.warnings,
        suggested_labels=suggested_labels(
            final_rev,
            list(deterministic.warnings),
            labels,
        ),
        file_categories=deterministic.file_categories,
        split_hints=split_hints,
        reviewer_checklist=checklist,
    )


def zero_llm_cost_fields() -> tuple[bool, str | None, int | None, int | None, Decimal | None]:
    """Sentinel row for deterministic-only persistence (issue #63)."""

    return False, None, None, None, None
