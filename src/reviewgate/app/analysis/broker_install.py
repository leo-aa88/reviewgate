"""Install the Dramatiq Redis broker for API and worker processes.

The GitHub webhook handler (issue #33) and the Dramatiq worker entrypoint
(issue #30) call :func:`install_redis_broker` before any actor ``.send``
operations so messages land in the same Redis-backed queue.
"""

from __future__ import annotations

import threading

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from reviewgate.app.settings import AppSettings

_last_installed_redis_url: str | None = None
_install_lock = threading.Lock()


def install_redis_broker(settings: AppSettings) -> None:
    """Configure the process-global Dramatiq broker backed by Redis.

    Idempotent for repeated calls with the same ``redis_url`` in one process
    (e.g. multiple webhook deliveries). A lock serializes installation so
    concurrent requests cannot leave Dramatiq bound to a stale broker.

    Args:
        settings: Loaded settings; ``redis_url`` must be non-empty.

    Raises:
        RuntimeError: If ``redis_url`` is missing.
    """

    global _last_installed_redis_url

    if settings.redis_url is None:
        msg = (
            "REVIEWGATE_REDIS_URL must be set before configuring the Dramatiq "
            "broker (see docs/QUICKSTART.md)."
        )
        raise RuntimeError(msg)

    url = str(settings.redis_url)
    with _install_lock:
        if _last_installed_redis_url == url:
            return
        dramatiq.set_broker(RedisBroker(url=url))
        _last_installed_redis_url = url
