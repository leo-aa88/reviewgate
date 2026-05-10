"""Install the Dramatiq Redis broker for API and worker processes.

Both the FastAPI app (issue #33) and the Dramatiq worker entrypoint (issue #30)
must call :func:`install_redis_broker` before any actor ``.send`` operations so
messages land in the same Redis-backed queue.
"""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from reviewgate.app.settings import AppSettings


def install_redis_broker(settings: AppSettings) -> None:
    """Configure the process-global Dramatiq broker backed by Redis.

    Args:
        settings: Loaded settings; ``redis_url`` must be non-empty.

    Raises:
        RuntimeError: If ``redis_url`` is missing.
    """

    if settings.redis_url is None:
        msg = (
            "REVIEWGATE_REDIS_URL must be set before configuring the Dramatiq "
            "broker (see docs/QUICKSTART.md)."
        )
        raise RuntimeError(msg)
    dramatiq.set_broker(RedisBroker(url=settings.redis_url))
