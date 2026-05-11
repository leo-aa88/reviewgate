"""Per-installation and per-repository analysis counters (issue #49).

``docs/DESIGN.md`` §22.2 beta defaults: **500** analyses per calendar day (UTC)
per GitHub installation and **100** per GitHub repository. Counters live in Redis
with day-bucket keys; exceeding a cap returns a dedicated outcome so workers can
skip work without touching Postgres (degraded, safe behavior).

When Redis is unavailable or counters cannot be updated, the limiter fails open
with ``ok`` so production is not hard-blocked by transient cache outages.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Final, Literal

import redis.exceptions

from reviewgate.app.redis_client import connect_redis
from reviewgate.app.settings import AppSettings

logger = logging.getLogger(__name__)

_MAX_ANALYSES_PER_INSTALLATION_PER_DAY: Final[int] = 500
_MAX_ANALYSES_PER_REPOSITORY_PER_DAY: Final[int] = 100
#: Keep keys past UTC midnight so ``EXPIRE`` does not race with the next bucket.
_COUNTER_KEY_TTL_SECONDS: Final[int] = 3 * 24 * 60 * 60

AnalysisRateLimitOutcome = Literal["ok", "installation_exceeded", "repository_exceeded"]


def _utc_day_bucket() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


def _installation_counter_key(github_installation_id: int) -> str:
    return (
        "reviewgate:rl:v1:installation:"
        f"{github_installation_id}:{_utc_day_bucket()}"
    )


def _repository_counter_key(github_repository_id: int) -> str:
    return (
        "reviewgate:rl:v1:repository:"
        f"{github_repository_id}:{_utc_day_bucket()}"
    )


def check_analysis_rate_limits(
    settings: AppSettings,
    *,
    github_installation_id: int,
    github_repository_id: int,
) -> AnalysisRateLimitOutcome:
    """Atomically increment daily counters and report whether limits allow work."""

    if github_installation_id < 1 or github_repository_id < 1:
        return "ok"

    client = connect_redis(settings)
    if client is None:
        return "ok"

    inst_key = _installation_counter_key(github_installation_id)
    repo_key = _repository_counter_key(github_repository_id)

    try:
        inst_count = int(client.incr(inst_key))
        if inst_count == 1:
            client.expire(inst_key, _COUNTER_KEY_TTL_SECONDS)
        if inst_count > _MAX_ANALYSES_PER_INSTALLATION_PER_DAY:
            logger.warning(
                "analysis rate limit: installation %s exceeded daily cap (%s)",
                github_installation_id,
                _MAX_ANALYSES_PER_INSTALLATION_PER_DAY,
            )
            return "installation_exceeded"

        repo_count = int(client.incr(repo_key))
        if repo_count == 1:
            client.expire(repo_key, _COUNTER_KEY_TTL_SECONDS)
        if repo_count > _MAX_ANALYSES_PER_REPOSITORY_PER_DAY:
            logger.warning(
                "analysis rate limit: repository %s exceeded daily cap (%s)",
                github_repository_id,
                _MAX_ANALYSES_PER_REPOSITORY_PER_DAY,
            )
            return "repository_exceeded"

        return "ok"
    except (TypeError, ValueError):
        return "ok"
    except redis.exceptions.RedisError as exc:
        logger.info("analysis rate limit counters skipped (Redis error: %s)", exc)
        return "ok"
    finally:
        client.close()
