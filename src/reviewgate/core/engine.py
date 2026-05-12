"""Deterministic engine entry point (docs/DESIGN.md \u00a710).

This module owns the public ``analyze`` function that the CLI, the
GitHub Action, and the hosted App worker all call. It is the documented
boundary of \u00a74.1: pure, no I/O, no GitHub or LLM dependencies, and a
stable signature ``EngineInput -> ReviewabilityReport``.

The engine composes the deterministic heuristics that ship in Milestone
2: file categorisation (\u00a710.5), ``human_loc_changed`` (\u00a710.4) and size warnings
(\u00a710.3 / \u00a710.4), and the baseline reviewability aggregation (\u00a710.13).
Heuristics that are still in flight (#11 weak body, #12 linked issue,
#13 risky-paths-without-context, #14 mixed concern) plug in here as
they land without changing the public signature.
"""

from __future__ import annotations

from pydantic import JsonValue, ValidationError

from .aggregate import baseline_reviewability
from .automation_pr import finalize_size_stats_for_pr_author
from .categorizer import Categorizer
from .config import DEFAULT_RISKY_PATHS, ReviewGateConfig
from .count_warnings import warn_threshold_count_warnings
from .ignored_paths import filter_out_ignored_paths
from .linked_issue import linked_issue_warning
from .mixed_concern import mixed_concern_warning
from .pr_body import weak_body_warning
from .report import suggested_labels
from .risky_paths import risky_paths_warning
from .schemas import ChangedFile, EngineInput, EngineWarning, PRRecord, ReviewabilityReport
from .size import compute_size_stats, size_warnings
from .tests_coverage import missing_tests_for_source_warning


def _merge_size_and_automation_stats(
    size_dump: dict[str, JsonValue],
    automation: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Merge §10.4 :class:`SizeStats` JSON with automation extras.

    Raises:
        RuntimeError: When key sets overlap (contract drift between producers).

    Returns:
        A new dict suitable for ``ReviewabilityReport.stats``.
    """

    overlap = size_dump.keys() & automation.keys()
    if overlap:
        raise RuntimeError(
            "ReviewGate internal error: SizeStats fields overlap automation stats "
            f"(collision keys: {sorted(overlap)}). Resolve naming between "
            "`reviewgate.core.size.SizeStats` and `reviewgate.core.automation_pr`."
        )
    merged: dict[str, JsonValue] = dict(size_dump)
    merged.update(automation)
    return merged


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
        * ``stats`` -- the \u00a710.4 size totals (raw, excluded,
          ``human_loc_changed``) plus ``files_changed`` / ``additions`` /
          ``deletions``, merged with §10.4.1–§10.4.2 keys from
          :mod:`reviewgate.core.automation_pr` (``pr_author_kind``,
          ``pr_author_login``, optional manifest-only flags).
        * ``warnings`` -- size warnings from \u00a710.3 thresholds; further
          heuristics (#11-#14) extend this list as they land.
        * ``reviewability`` -- result of
          :func:`baseline_reviewability` over those warnings (\u00a710.13).
    """

    pr = engine_input.pr
    config = _resolve_config(engine_input)
    risky_patterns = config.risky_paths or list(DEFAULT_RISKY_PATHS)

    original_files = engine_input.files
    active_files = filter_out_ignored_paths(original_files, config.ignored_paths)
    pr_for_stats = _pr_record_for_active_files(
        pr,
        active_files,
        original_file_count=len(original_files),
    )

    categorizer = Categorizer(risky_patterns=risky_patterns)
    file_categories = categorizer.categorize_all(active_files)

    base_stats = compute_size_stats(
        additions=pr_for_stats.additions,
        deletions=pr_for_stats.deletions,
        file_categories=file_categories,
    )
    stats, automation_stats = finalize_size_stats_for_pr_author(
        base_stats,
        author=pr_for_stats.author,
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

    warnings.extend(
        warn_threshold_count_warnings(file_categories, config.thresholds.warn),
    )

    if config.policy.require_human_summary:
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

    risky_warning = risky_paths_warning(
        file_categories,
        pr.body,
        fail_on_risky_paths_without_context=(config.policy.fail_on_risky_paths_without_context),
    )
    if risky_warning is not None:
        warnings.append(risky_warning)

    mixed_warning = mixed_concern_warning(file_categories)
    if mixed_warning is not None:
        warnings.append(mixed_warning)

    tests_warning = missing_tests_for_source_warning(file_categories)
    if tests_warning is not None:
        warnings.append(tests_warning)

    verdict = baseline_reviewability(warnings)
    stats_payload = _merge_size_and_automation_stats(
        stats.model_dump(mode="json"),
        automation_stats,
    )
    return ReviewabilityReport(
        reviewability=verdict,
        stats=stats_payload,
        warnings=warnings,
        suggested_labels=suggested_labels(verdict, warnings, config.labels),
        file_categories=file_categories,
    )


def _pr_record_for_active_files(
    pr: PRRecord,
    active_files: list[ChangedFile],
    *,
    original_file_count: int,
) -> PRRecord:
    """Shrink PR aggregate stats only when ``ignored_paths`` removed files."""

    if len(active_files) == original_file_count:
        return pr
    additions = sum(f.additions for f in active_files)
    deletions = sum(f.deletions for f in active_files)
    return pr.model_copy(
        update={
            "additions": additions,
            "deletions": deletions,
            "changed_files": len(active_files),
        },
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
