"""Dramatiq actors for hosted PR analysis (stub until issue #50).

``docs/DESIGN.md`` §13.7 recommends Dramatiq + Redis so webhooks never block on
analysis or LLM calls. This module defines **actors** (analysis stub plus housekeeping); the Redis broker is
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

from typing import Final

import dramatiq

from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.webhook_purge import purge_webhook_deliveries_older_than

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
    """

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
