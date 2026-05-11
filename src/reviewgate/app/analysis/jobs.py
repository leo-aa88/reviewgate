"""Dramatiq actors for hosted PR analysis (``docs/DESIGN.md`` §13.7; issues #46–#54).

``docs/DESIGN.md`` §13.7 recommends Dramatiq + Redis so webhooks never block on
analysis or LLM calls. This module defines **actors** (PR analysis plus
housekeeping); the Redis broker is
installed via :func:`reviewgate.app.analysis.broker_install.install_redis_broker`
from the GitHub webhook handler (issue #33) or in
:mod:`reviewgate.app.analysis.worker_app` before this module is first imported
by the Dramatiq CLI.

Retry and backoff defaults target GitHub and LLM rate limits: a modest number
of retries with exponentially capped backoff (milliseconds, Dramatiq
convention). Retriable GitHub HTTP failures re-raise so Dramatiq can retry.

Example:
    Unit tests can register actors against a stub broker::

        import dramatiq
        from dramatiq.brokers.stub import StubBroker

        dramatiq.set_broker(StubBroker())
        from reviewgate.app.analysis import jobs

        jobs.run_pr_analysis_stub({"pull_number": 1})
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Final

import dramatiq
import httpx

from reviewgate.app.analysis.hosted_github_outputs import publish_hosted_pr_github_feedback
from reviewgate.app.analysis.pipeline import (
    AnalysisPipelineUserError,
    HostRepoContext,
    resolve_host_repo_context,
    run_pr_analysis_for_natural_key,
)
from reviewgate.app.llm.stage import maybe_apply_hosted_llm_stage
from reviewgate.app.analysis.result_cache import (
    get_cached_final_report,
    set_cached_final_report,
)
from reviewgate.app.analysis.worker_job_lock import worker_job_lock_hold
from reviewgate.app.github.auth import GitHubAppAuthError
from reviewgate.app.github.client import GitHubRestError
from reviewgate.app.rate_limit.limiter import check_analysis_rate_limits
from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.repositories import (
    AnalysisNaturalKey,
    begin_analysis_for_job_start,
    insert_analysis_report,
    mark_analysis_completed,
    mark_analysis_failed,
    parse_analysis_job_natural_key,
    update_analysis_pr_size_fields,
)
from reviewgate.core.config import ReviewGateConfig
from reviewgate.core.schemas import ReviewabilityReport
from reviewgate.app.storage.webhook_purge import purge_webhook_deliveries_older_than
from reviewgate.app.webhooks.enqueue_policy import installation_repository_may_enqueue_jobs

logger = logging.getLogger(__name__)

_MAX_RETRIES: Final[int] = 5
_MIN_BACKOFF_MS: Final[int] = 30_000
_MAX_BACKOFF_MS: Final[int] = 300_000
_TIME_LIMIT_MS: Final[int] = 900_000
_PURGE_TIME_LIMIT_MS: Final[int] = 300_000


def _non_negative_stat(stats: dict[str, object], key: str) -> int:
    raw = stats.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


@dramatiq.actor(
    max_retries=_MAX_RETRIES,
    min_backoff=_MIN_BACKOFF_MS,
    max_backoff=_MAX_BACKOFF_MS,
    time_limit=_TIME_LIMIT_MS,
)
def run_pr_analysis_stub(payload: dict[str, object]) -> None:
    """Run hosted PR analysis: GitHub fetch, deterministic core, Postgres persistence.

    Args:
        payload: Job envelope from the webhook handler (installation ids,
            optional ``reviewgate_*`` natural-key fields). When ``github_*`` ids
            are set (issue #36), the actor skips work for soft-deleted
            installations or inactive repositories. When all ``reviewgate_*``
            keys are present, issue #50 loads PR data from GitHub, runs
            :func:`reviewgate.core.engine.analyze`, writes ``analysis_reports``,
            and completes the ``analyses`` row unless another worker holds the row
            (``already_running``) or it is ``already_completed``. Redis final-result
            cache (issue #48) and §22.2 rate limits (issue #49) apply when
            configured. After a successful DB commit, §14.1 ``mode`` gates hosted
            GitHub comments, labels, and check runs via
            :func:`reviewgate.app.analysis.hosted_github_outputs.publish_hosted_pr_github_feedback`.
    """

    settings = AppSettings()
    raw_inst = payload.get("github_installation_id")
    raw_repo = payload.get("github_repository_id")
    natural = parse_analysis_job_natural_key(payload)
    need_installation_guard = (
        not isinstance(raw_inst, bool)
        and isinstance(raw_inst, int)
        and not isinstance(raw_repo, bool)
        and isinstance(raw_repo, int)
    )
    engine = create_engine_from_settings(settings)
    if engine is None or not (need_installation_guard or natural is not None):
        del payload
        return

    # ``docs/DESIGN.md`` §13.7 ordering (issue #47): mechanism #1 (delivery dedupe)
    # runs in the HTTP handler before enqueue; mechanism #2 (skip vs completed
    # ``analyses``) also runs there; mechanism #3 (Redis worker lock) wraps the
    # Postgres lifecycle after the installation guard (issue #36) so soft-deleted
    # installs still short-circuit before Redis.
    lock_ctx = (
        worker_job_lock_hold(str(settings.redis_url), natural)
        if natural is not None and settings.redis_url is not None
        else nullcontext(True)
    )

    session_factory = create_session_factory(engine)
    publish_work: (
        tuple[
            HostRepoContext,
            AnalysisNaturalKey,
            ReviewabilityReport,
            ReviewGateConfig,
        ]
        | None
    ) = None
    with session_factory() as session:
        if need_installation_guard:
            if not installation_repository_may_enqueue_jobs(
                session,
                github_installation_id=raw_inst,
                github_repository_id=raw_repo,
            ):
                return

        if need_installation_guard and settings.redis_url is not None:
            outcome = check_analysis_rate_limits(
                settings,
                github_installation_id=raw_inst,
                github_repository_id=raw_repo,
            )
            if outcome != "ok":
                return

        with lock_ctx as lock_acquired:
            if (
                natural is not None
                and settings.redis_url is not None
                and not lock_acquired
            ):
                return

            # §13.6 final-result cache (issue #48): composite key always includes
            # ``head_sha`` via :func:`~reviewgate.app.analysis.cache.analysis_cache_key`.
            if natural is not None and settings.redis_url is not None:
                cached = get_cached_final_report(settings, natural)
                if cached is not None:
                    del payload
                    return

            if natural is not None:
                analysis_id, begin_kind = begin_analysis_for_job_start(
                    session,
                    natural,
                )
                if begin_kind in ("already_completed", "already_running"):
                    del payload
                    return

                ctx = resolve_host_repo_context(session, natural.repository_id)
                if ctx is None:
                    mark_analysis_failed(
                        session,
                        analysis_id,
                        error_code="missing_repository_context",
                    )
                elif (
                    need_installation_guard
                    and ctx.github_installation_id != raw_inst
                ):
                    mark_analysis_failed(
                        session,
                        analysis_id,
                        error_code="installation_context_mismatch",
                    )
                else:
                    try:
                        with httpx.Client(timeout=30.0) as http_client:
                            (
                                report,
                                effective_config,
                                pipeline_artifacts,
                            ) = run_pr_analysis_for_natural_key(
                                settings,
                                natural,
                                ctx,
                                http_client=http_client,
                            )
                    except GitHubRestError as exc:
                        if exc.retriable:
                            raise
                        mark_analysis_failed(
                            session,
                            analysis_id,
                            error_code="github_rest",
                        )
                    except GitHubAppAuthError:
                        mark_analysis_failed(
                            session,
                            analysis_id,
                            error_code="github_app_auth",
                        )
                    except AnalysisPipelineUserError as exc:
                        mark_analysis_failed(
                            session,
                            analysis_id,
                            error_code=exc.error_code,
                        )
                    except httpx.HTTPError:
                        raise
                    else:
                        deterministic_dump = report.model_dump(mode="json")
                        llm_outcome = maybe_apply_hosted_llm_stage(
                            settings,
                            deterministic_report=report,
                            effective_config=effective_config,
                            artifacts=pipeline_artifacts,
                        )
                        final_report = llm_outcome.report
                        final_dump = final_report.model_dump(mode="json")
                        stats_obj = final_dump.get("stats")
                        stats_map = (
                            stats_obj
                            if isinstance(stats_obj, dict)
                            else {}
                        )
                        update_analysis_pr_size_fields(
                            session,
                            analysis_id,
                            files_changed=_non_negative_stat(
                                stats_map,
                                "files_changed",
                            ),
                            raw_loc_changed=_non_negative_stat(
                                stats_map,
                                "raw_loc_changed",
                            ),
                            human_loc_changed=_non_negative_stat(
                                stats_map,
                                "human_loc_changed",
                            ),
                        )
                        mark_analysis_completed(
                            session,
                            analysis_id,
                            reviewability=final_report.reviewability,
                        )
                        insert_analysis_report(
                            session,
                            analysis_id,
                            report_json=final_dump,
                            deterministic_json=deterministic_dump,
                            llm_used=llm_outcome.llm_used,
                            llm_provider=llm_outcome.llm_provider,
                            input_tokens=llm_outcome.input_tokens,
                            output_tokens=llm_outcome.output_tokens,
                            estimated_cost_usd=llm_outcome.estimated_cost_usd,
                        )
                        if settings.redis_url is not None:
                            set_cached_final_report(
                                settings,
                                natural,
                                {
                                    "reviewability": final_report.reviewability,
                                    "stats": final_report.stats,
                                },
                            )
                        publish_work = (ctx, natural, final_report, effective_config)
            session.commit()

    if publish_work is not None:
        pub_ctx, pub_key, pub_report, pub_cfg = publish_work
        try:
            with httpx.Client(timeout=30.0) as http_client:
                publish_hosted_pr_github_feedback(
                    settings,
                    ctx=pub_ctx,
                    key=pub_key,
                    report=pub_report,
                    config=pub_cfg,
                    http_client=http_client,
                )
        except Exception:
            logger.exception("publish_hosted_pr_github_feedback_unexpected_failure")

    del payload


@dramatiq.actor(max_retries=2, time_limit=_PURGE_TIME_LIMIT_MS)
def purge_old_webhook_deliveries(_payload: dict[str, object] | None = None) -> None:
    """Delete ``webhook_deliveries`` rows older than 30 days (§16.1).

    Operators should run this daily via cron, Kubernetes CronJob, or a Dramatiq
    message sent from a scheduler they control. The actor is safe to invoke
    synchronously with :meth:`dramatiq.Actor.fn` from a maintenance job.

    Args:
        _payload: Unused; kept so callers can ``send({})`` like other actors.
    """

    del _payload
    settings = AppSettings()
    engine = create_engine_from_settings(settings)
    if engine is None:
        return
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        purge_webhook_deliveries_older_than(session)
        session.commit()
