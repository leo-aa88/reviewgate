"""Dramatiq worker bootstrap: install the Redis broker, then register job actors.

Point the Dramatiq CLI at this module so the broker is configured **before**
actors from :mod:`reviewgate.app.analysis.jobs` are imported::

    python -m dramatiq reviewgate.app.analysis.worker_app

The companion console script ``reviewgate-worker`` wraps the same invocation
(see :mod:`reviewgate.app.analysis.worker_cli`).

Raises:
    RuntimeError: If ``REVIEWGATE_REDIS_URL`` is unset. Workers always require
        Redis in real deployments; unit tests should use
        :class:`dramatiq.brokers.stub.StubBroker` with :mod:`~reviewgate.app.analysis.jobs`
        directly instead of importing this module.
"""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from reviewgate.app.settings import AppSettings

_settings = AppSettings()
if _settings.redis_url is None:
    msg = (
        "REVIEWGATE_REDIS_URL must be set before starting Dramatiq workers "
        "(see docs/QUICKSTART.md, hosted dev section)."
    )
    raise RuntimeError(msg)

dramatiq.set_broker(RedisBroker(url=_settings.redis_url))

# Broker must exist before actor modules import (Dramatiq registers on import).
from reviewgate.app.analysis import jobs as _reviewgate_analysis_jobs  # noqa: E402,F401
