"""Per-installation and per-repository analysis counters (issue #49).

``docs/DESIGN.md`` §22.2 beta defaults: **500** analyses per calendar day (UTC)
per GitHub installation and **100** per GitHub repository. Counters live in Redis
with day-bucket keys; exceeding a cap returns a dedicated outcome so workers can
skip work without touching Postgres (degraded, safe behavior).

When Redis is unavailable or counters cannot be updated, the limiter fails open
with ``ok`` so production is not hard-blocked by transient cache outages.

If the installation counter was incremented and the repository step then fails,
exceeds its cap, or the installation cap is already exceeded for this call, the
installation increment is rolled back so rejected jobs do not permanently
consume installation quota (PR #114 review).
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


def _touch_counter_ttl(client: object, key: str) -> None:
    """Best-effort ``EXPIRE``; TTL loss must not fail the rate-limit check."""

    try:
        client.expire(key, _COUNTER_KEY_TTL_SECONDS)
    except redis.exceptions.RedisError as exc:
        logger.info("analysis rate limit: expire skipped for %s (%s)", key, exc)


def _rollback_installation_counter(client: object, inst_key: str) -> None:
    """Undo one installation ``INCR`` when this request must not consume quota.

    Used when the installation cap is exceeded, the repository leg fails or is
    over cap, or Redis errors occur after the installation counter was bumped.
    """

    try:
        client.decr(inst_key)
    except redis.exceptions.RedisError as exc:
        logger.warning(
            "analysis rate limit: installation rollback failed for %s (%s)",
            inst_key,
            exc,
        )


def check_analysis_rate_limits(
    settings: AppSettings,
    *,
    github_installation_id: int,
    github_repository_id: int,
) -> AnalysisRateLimitOutcome:
    """Increment daily counters and report whether limits allow work.

    The installation counter is incremented first. If the installation cap is
    exceeded, if the repository counter cannot be updated, or if it is over its
    daily cap, the installation increment for this call is rolled back so
    rejected or skipped analyses do not leak installation quota.
    """

    if github_installation_id < 1 or github_repository_id < 1:
        return "ok"

    client = connect_redis(settings)
    if client is None:
        return "ok"

    inst_key = _installation_counter_key(github_installation_id)
    repo_key = _repository_counter_key(github_repository_id)
    installation_incremented = False

    try:
        inst_count = int(client.incr(inst_key))
        installation_incremented = True
        if inst_count == 1:
            _touch_counter_ttl(client, inst_key)
        if inst_count > _MAX_ANALYSES_PER_INSTALLATION_PER_DAY:
            logger.warning(
                "analysis rate limit: installation %s exceeded daily cap (%s)",
                github_installation_id,
                _MAX_ANALYSES_PER_INSTALLATION_PER_DAY,
            )
            _rollback_installation_counter(client, inst_key)
            return "installation_exceeded"

        repo_raw = client.incr(repo_key)
        repo_count = int(repo_raw)
        if repo_count == 1:
            _touch_counter_ttl(client, repo_key)
        if repo_count > _MAX_ANALYSES_PER_REPOSITORY_PER_DAY:
            logger.warning(
                "analysis rate limit: repository %s exceeded daily cap (%s)",
                github_repository_id,
                _MAX_ANALYSES_PER_REPOSITORY_PER_DAY,
            )
            _rollback_installation_counter(client, inst_key)
            return "repository_exceeded"

        return "ok"
    except (TypeError, ValueError):
        if installation_incremented:
            _rollback_installation_counter(client, inst_key)
        return "ok"
    except redis.exceptions.RedisError as exc:
        if installation_incremented:
            _rollback_installation_counter(client, inst_key)
        logger.info("analysis rate limit counters skipped (Redis error: %s)", exc)
        return "ok"
    finally:
        client.close()
