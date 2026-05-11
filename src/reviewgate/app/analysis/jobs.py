"""Dramatiq actors for hosted PR analysis (stub until issue #50).

``docs/DESIGN.md`` §13.7 recommends Dramatiq + Redis so webhooks never block on
analysis or LLM calls. This module defines **actors** (analysis stub plus
housekeeping); the Redis broker is
installed via :func:`reviewgate.app.analysis.broker_install.install_redis_broker`
from the GitHub webhook handler (issue #33) or in
:mod:`reviewgate.app.analysis.worker_app` before this module is first imported
by the Dramatiq CLI.

Retry and backoff defaults target GitHub and LLM rate limits: a modest number
of retries with exponentially capped backoff (milliseconds, Dramatiq
convention). Tune per-actor when the real pipeline lands in issue #50.

Example:
    Unit tests can register actors against a stub broker::

        import dramatiq
        from dramatiq.brokers.stub import StubBroker

        dramatiq.set_broker(StubBroker())
        from reviewgate.app.analysis import jobs

        jobs.run_pr_analysis_stub({"pull_number": 1})
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Final

import dramatiq

from reviewgate.app.analysis.result_cache import (
    get_cached_final_report,
    set_cached_final_report,
)
from reviewgate.app.analysis.worker_job_lock import worker_job_lock_hold
from reviewgate.app.rate_limit.limiter import check_analysis_rate_limits
from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.repositories import (
    begin_analysis_for_job_start,
    mark_analysis_completed,
    parse_analysis_job_natural_key,
)
from reviewgate.app.storage.webhook_purge import purge_webhook_deliveries_older_than
from reviewgate.app.webhooks.enqueue_policy import installation_repository_may_enqueue_jobs

_MAX_RETRIES: Final[int] = 5
_MIN_BACKOFF_MS: Final[int] = 30_000
_MAX_BACKOFF_MS: Final[int] = 300_000
_TIME_LIMIT_MS: Final[int] = 900_000
_PURGE_TIME_LIMIT_MS: Final[int] = 300_000


@dramatiq.actor(
    max_retries=_MAX_RETRIES,
    min_backoff=_MIN_BACKOFF_MS,
    max_backoff=_MAX_BACKOFF_MS,
    time_limit=_TIME_LIMIT_MS,
)
def run_pr_analysis_stub(payload: dict[str, object]) -> None:
    """Placeholder actor; issue #50 replaces this with the real pipeline.

    Args:
        payload: Opaque job envelope (repository id, PR number, head SHA, etc.).
            Kept as ``dict[str, object]`` until the worker contract is frozen.
            When ``github_installation_id`` and ``github_repository_id`` are set
            (issue #36), the actor skips work for soft-deleted installations or
            inactive repositories before the real pipeline exists. When all
            optional ``reviewgate_repository_id`` (UUID string or instance),
            ``reviewgate_pull_number``, ``reviewgate_head_sha``,
            ``reviewgate_config_hash``, and ``reviewgate_pr_metadata_hash`` keys
            are present, the stub persists ``analyses`` lifecycle rows per issue
            #46 (``running`` then ``completed`` with reviewability ``PASS``), except
            when another worker already holds ``running`` (``already_running``) or
            the row is ``already_completed``. When Redis is configured, §13.6
            final-result cache (issue #48) is consulted after the worker lock and
            populated after a fresh ``completed`` transition. §22.2 Redis counters
            (issue #49) run after the installation guard when Redis is configured.
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
                if begin_kind not in ("already_completed", "already_running"):
                    mark_analysis_completed(
                        session,
                        analysis_id,
                        reviewability="PASS",
                    )
                    if settings.redis_url is not None:
                        set_cached_final_report(
                            settings,
                            natural,
                            {
                                "reviewability": "PASS",
                                "result_cache_stub": True,
                            },
                        )
            session.commit()

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
