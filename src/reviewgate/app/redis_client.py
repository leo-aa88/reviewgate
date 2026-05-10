"""Synchronous Redis client factory for the hosted app (``docs/DESIGN.md`` §15).

Uses the `redis-py <https://redis.readthedocs.io/>`_ driver behind the optional
``app`` extra. Callers obtain a :class:`redis.Redis` instance from
:class:`~reviewgate.app.settings.AppSettings` without importing Redis-specific
types at import time in unrelated modules.

Example:
    Obtaining a client when ``REVIEWGATE_REDIS_URL`` is configured::

        from reviewgate.app.redis_client import connect_redis
        from reviewgate.app.settings import AppSettings

        client = connect_redis(AppSettings())
        if client is not None:
            client.ping()
"""

from __future__ import annotations

import redis

from reviewgate.app.settings import AppSettings


def connect_redis(settings: AppSettings) -> redis.Redis | None:
    """Return a Redis client when ``settings.redis_url`` is set, else ``None``.

    Args:
        settings: Loaded :class:`AppSettings` (typically from the environment).

    Returns:
        A decoded-responses Redis client, or ``None`` if no URL was configured.

    Raises:
        redis.exceptions.ConnectionError: Delegated from the driver when the
            server is unreachable (only after a URL is present).
    """

    if settings.redis_url is None:
        return None

    return redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
