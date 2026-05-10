"""Daily housekeeping hook for ``webhook_deliveries`` retention (issue #34)."""

from __future__ import annotations

import logging
import os
import threading
import time


def schedule_daily_webhook_purge() -> None:
    """Start a daemon thread that runs :func:`purge_old_webhook_deliveries.fn` daily.

    Disabled when ``REVIEWGATE_DISABLE_WEBHOOK_PURGE_SCHEDULER`` is ``1``.
    Invoked from :mod:`reviewgate.app.analysis.worker_app` after Dramatiq actors
    load so :func:`~reviewgate.app.analysis.jobs.purge_old_webhook_deliveries`
    is registered.
    """

    if os.environ.get("REVIEWGATE_DISABLE_WEBHOOK_PURGE_SCHEDULER") == "1":
        return

    logger = logging.getLogger(__name__)

    def _target() -> None:
        from reviewgate.app.analysis import jobs as reviewgate_jobs

        while True:
            try:
                reviewgate_jobs.purge_old_webhook_deliveries.fn({})
            except Exception:
                logger.exception("purge_old_webhook_deliveries failed")
            time.sleep(24 * 3600)

    threading.Thread(
        target=_target,
        name="reviewgate-webhook-purge",
        daemon=True,
    ).start()
