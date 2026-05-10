"""Deterministic engine entry point (docs/DESIGN.md \u00a710).

This module owns the public ``analyze`` function that the CLI, the
GitHub Action, and the hosted App worker all call. It is the documented
boundary of \u00a74.1: pure, no I/O, no GitHub or LLM dependencies, and a
stable signature ``EngineInput -> ReviewabilityReport``.

The engine composes the deterministic heuristics that ship in Milestone
2: file categorisation (\u00a710.5), human-authored LOC and size warnings
(\u00a710.3 / \u00a710.4), and the baseline reviewability aggregation (\u00a710.13).
Heuristics that are still in flight (#11 weak body, #12 linked issue,
#13 risky-paths-without-context, #14 mixed concern) plug in here as
they land without changing the public signature.
"""

from __future__ import annotations

from pydantic import ValidationError

from .aggregate import baseline_reviewability
from .categorizer import Categorizer
from .config import DEFAULT_RISKY_PATHS, ReviewGateConfig
from .linked_issue import linked_issue_warning
from .pr_body import weak_body_warning
from .schemas import EngineInput, EngineWarning, ReviewabilityReport
from .size import compute_size_stats, size_warnings


def analyze(engine_input: EngineInput) -> ReviewabilityReport:
    """Run the deterministic engine over a normalized PR input (\u00a710).

    Args:
        engine_input: A validated :class:`EngineInput` matching the
            \u00a710.1 schema. Validation is the caller's responsibility
            (the CLI does it via :class:`pydantic.BaseModel.model_validate`).

    Returns:
        A :class:`ReviewabilityReport` matching the \u00a710.2 schema. The
        report carries:

        * ``file_categories`` -- one row per changed file (\u00a710.5).
        * ``stats`` -- the \u00a710.4 size totals (raw, excluded, human) plus
          ``files_changed`` / ``additions`` / ``deletions``.
        * ``warnings`` -- size warnings from \u00a710.3 thresholds; further
          heuristics (#11-#14) extend this list as they land.
        * ``reviewability`` -- result of
          :func:`baseline_reviewability` over those warnings (\u00a710.13).
    """

    pr = engine_input.pr
    config = _resolve_config(engine_input)
    risky_patterns = config.risky_paths or list(DEFAULT_RISKY_PATHS)

    categorizer = Categorizer(risky_patterns=risky_patterns)
    file_categories = categorizer.categorize_all(engine_input.files)

    stats = compute_size_stats(
        additions=pr.additions,
        deletions=pr.deletions,
        file_categories=file_categories,
    )

    warnings: list[EngineWarning] = []
    warnings.extend(
        size_warnings(
            stats,
            warn_files_changed=config.thresholds.warn.files_changed,
            fail_files_changed=config.thresholds.fail.files_changed,
            warn_human_loc_changed=config.thresholds.warn.human_loc_changed,
            fail_human_loc_changed=config.thresholds.fail.human_loc_changed,
        ),
    )

    body_warning = weak_body_warning(pr.body)
    if body_warning is not None:
        warnings.append(body_warning)

    issue_warning = linked_issue_warning(
        pr.title,
        pr.body,
        require_linked_issue=config.policy.require_linked_issue,
    )
    if issue_warning is not None:
        warnings.append(issue_warning)

    return ReviewabilityReport(
        reviewability=baseline_reviewability(warnings),
        stats=stats.model_dump(),
        warnings=warnings,
        file_categories=file_categories,
    )


def _resolve_config(engine_input: EngineInput) -> ReviewGateConfig:
    """Materialise an effective :class:`ReviewGateConfig` for this run.

    The \u00a710.1 ``EngineInput.config`` field is a free-form
    ``dict[str, JsonValue]`` so the engine can be fed by both the CLI
    (JSON fixture) and the hosted App (already-validated config). When
    it parses cleanly, use it; on any validation failure fall back to
    all-defaults so the engine still produces a report rather than
    raising.
    """

    raw = engine_input.config
    if not raw:
        return ReviewGateConfig()
    try:
        return ReviewGateConfig.model_validate(raw)
    except ValidationError:
        # \u00a712 already specifies that malformed config never crashes the
        # analysis. ``load_config`` is the place that records the
        # accompanying warning on the surrounding pipeline; here we just
        # ensure the engine keeps running with defaults. Pydantic wraps
        # every input-shape failure (non-mapping at the top level,
        # unknown keys under ``extra='forbid'``, wrong scalar types) in
        # :class:`ValidationError`, so a narrower except is sufficient.
        return ReviewGateConfig()


__all__ = ["analyze"]
