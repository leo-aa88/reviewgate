"""Dramatiq worker bootstrap: install the Redis broker, then register job actors.

Point the Dramatiq CLI at this module so the broker is configured **before**
actors from :mod:`reviewgate.app.analysis.jobs` are imported::

    python -m dramatiq reviewgate.app.analysis.worker_app

The companion console script ``reviewgate-worker`` wraps the same invocation
(see :mod:`reviewgate.app.analysis.worker_cli`).

Raises:
    RuntimeError: If ``REVIEWGATE_REDIS_URL`` is unset (see
        :func:`reviewgate.app.analysis.broker_install.install_redis_broker`).

A daemon thread enqueues :func:`reviewgate.app.analysis.jobs.purge_old_webhook_deliveries`
once per day (§16.1). Set ``REVIEWGATE_DISABLE_WEBHOOK_PURGE_SCHEDULER=1`` to
disable it (for example in specialized test harnesses).
"""

from __future__ import annotations

import logging
import os
import threading
import time

from reviewgate.app.analysis.broker_install import install_redis_broker
from reviewgate.app.settings import AppSettings

_settings = AppSettings()
install_redis_broker(_settings)

# Broker must exist before actor modules import (Dramatiq registers on import).
from reviewgate.app.analysis import jobs as _reviewgate_analysis_jobs  # noqa: E402,F401


def _schedule_daily_webhook_purge() -> None:
    """Background loop that enqueues §16.1 ``webhook_deliveries`` retention purges."""

    if os.environ.get("REVIEWGATE_DISABLE_WEBHOOK_PURGE_SCHEDULER") == "1":
        return

    logger = logging.getLogger(__name__)

    def _target() -> None:
        while True:
            try:
                # Call the actor function directly: ``.send`` is not thread-safe
                # from this background thread (Dramatiq broker is owned by worker
                # threads).
                _reviewgate_analysis_jobs.purge_old_webhook_deliveries.fn({})
            except Exception:
                logger.exception("purge_old_webhook_deliveries failed")
            time.sleep(24 * 3600)

    threading.Thread(
        target=_target,
        name="reviewgate-webhook-purge",
        daemon=True,
    ).start()


_schedule_daily_webhook_purge()
