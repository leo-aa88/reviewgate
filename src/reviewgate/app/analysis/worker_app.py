"""Dramatiq worker bootstrap: install the Redis broker, then register job actors.

Point the Dramatiq CLI at this module so the broker is configured **before**
actors from :mod:`reviewgate.app.analysis.jobs` are imported::

    python -m dramatiq reviewgate.app.analysis.worker_app

The companion console script ``reviewgate-worker`` wraps the same invocation
(see :mod:`reviewgate.app.analysis.worker_cli`).

Raises:
    RuntimeError: If ``REVIEWGATE_REDIS_URL`` is unset (see
        :func:`reviewgate.app.analysis.broker_install.install_redis_broker`).

Daily retention jobs:

* :func:`reviewgate.app.analysis.jobs.purge_old_webhook_deliveries` (issue #34)
  for ``webhook_deliveries``.
* :func:`reviewgate.app.analysis.jobs.purge_analyses_for_old_uninstalls` (GitHub #124)
  for installation-scoped ``analyses`` / ``analysis_reports`` after uninstall.

Schedule both with an external cron or orchestrator that invokes each actor's
``fn`` or ``send`` from a thread-safe context.
"""

from __future__ import annotations

from reviewgate.app.analysis.broker_install import install_redis_broker
from reviewgate.app.settings import AppSettings

_settings = AppSettings()
install_redis_broker(_settings)

# Broker must exist before actor modules import (Dramatiq registers on import).
from reviewgate.app.analysis import jobs as _reviewgate_analysis_jobs  # noqa: E402,F401
