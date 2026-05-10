"""Dramatiq worker bootstrap: install the Redis broker, then register job actors.

Point the Dramatiq CLI at this module so the broker is configured **before**
actors from :mod:`reviewgate.app.analysis.jobs` are imported::

    python -m dramatiq reviewgate.app.analysis.worker_app

The companion console script ``reviewgate-worker`` wraps the same invocation
(see :mod:`reviewgate.app.analysis.worker_cli`).

Raises:
    RuntimeError: If ``REVIEWGATE_REDIS_URL`` is unset (see
        :func:`reviewgate.app.analysis.broker_install.install_redis_broker`).

:func:`reviewgate.app.analysis.webhook_purge_scheduler.schedule_daily_webhook_purge`
starts a daemon thread that runs :func:`reviewgate.app.analysis.jobs.purge_old_webhook_deliveries`
once per day (§16.1). Set ``REVIEWGATE_DISABLE_WEBHOOK_PURGE_SCHEDULER=1`` to
disable it (for example in specialized test harnesses).
"""

from __future__ import annotations

from reviewgate.app.analysis.broker_install import install_redis_broker
from reviewgate.app.analysis.webhook_purge_scheduler import schedule_daily_webhook_purge
from reviewgate.app.settings import AppSettings

_settings = AppSettings()
install_redis_broker(_settings)

# Broker must exist before actor modules import (Dramatiq registers on import).
from reviewgate.app.analysis import jobs as _reviewgate_analysis_jobs  # noqa: E402,F401

schedule_daily_webhook_purge()
