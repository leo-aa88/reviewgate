"""Redis mutex for a single analysis worker run (``docs/DESIGN.md`` §13.7 #3).

Uses :func:`reviewgate.app.analysis.cache.worker_job_lock_key` with ``SET … NX EX``
and a random token value so only the holder can delete the key early. TTL matches
the Dramatiq actor ``time_limit`` on :func:`reviewgate.app.analysis.jobs.run_pr_analysis_stub`
(900 seconds).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from reviewgate.app.analysis.cache import worker_job_lock_key

if TYPE_CHECKING:
    from reviewgate.app.storage.repositories import AnalysisNaturalKey

_JOB_LOCK_TTL_SECONDS: Final[int] = 900

_RELEASE_IF_MATCH_LUA: Final[str] = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
"""


@contextmanager
def worker_job_lock_hold(
    redis_url: str | None,
    key: "AnalysisNaturalKey",
) -> Iterator[bool]:
    """Acquire the §13.7 worker lock when ``redis_url`` is set; else yield ``True``.

    Yields:
        ``True`` when this process may proceed with analysis work, or ``False``
        when another worker already holds the lock (caller should exit quietly).

    Raises:
        redis.exceptions.RedisError: When Redis is unreachable while acquiring or
            releasing the lock (callers should treat as infrastructure failure).
    """

    if not redis_url or not redis_url.strip():
        yield True
        return

    import redis as redis_sync

    lock_key = worker_job_lock_key(
        repository_id=key.repository_id,
        pull_number=key.pull_number,
        head_sha=key.head_sha,
        config_hash=key.config_hash,
        pr_metadata_hash=key.pr_metadata_hash,
    )
    token = uuid.uuid4().hex
    client = redis_sync.Redis.from_url(redis_url, decode_responses=True)
    try:
        acquired = bool(
            client.set(lock_key, token, nx=True, ex=_JOB_LOCK_TTL_SECONDS),
        )
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            client.eval(_RELEASE_IF_MATCH_LUA, 1, lock_key, token)
    finally:
        client.close()
