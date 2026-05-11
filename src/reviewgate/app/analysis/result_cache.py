"""Redis cache for **final** hosted analysis JSON (``docs/DESIGN.md`` §13.6, issue #48).

Only completed pipeline outputs are stored. Keys reuse
:func:`reviewgate.app.analysis.cache.analysis_cache_key`, which always includes
``head_sha`` as part of the composite identity, so intermediate GitHub fetches
without a stable head ref never share this cache namespace.

TTL is **24 hours** (86400 seconds) per §13.6.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Final

import redis.exceptions

from reviewgate.app.analysis.cache import analysis_cache_key
from reviewgate.app.redis_client import connect_redis
from reviewgate.app.settings import AppSettings

if TYPE_CHECKING:
    from reviewgate.app.storage.repositories import AnalysisNaturalKey

logger = logging.getLogger(__name__)

#: ``docs/DESIGN.md`` §13.6 — retain final cached analysis blobs for one day.
ANALYSIS_RESULT_CACHE_TTL_SECONDS: Final[int] = 24 * 60 * 60


def get_cached_final_report(
    settings: AppSettings,
    key: "AnalysisNaturalKey",
) -> dict[str, object] | None:
    """Return a deserialized final report when Redis holds a cache entry, else ``None``."""

    client = connect_redis(settings)
    if client is None:
        return None
    cache_key = analysis_cache_key(
        repository_id=key.repository_id,
        pull_number=key.pull_number,
        head_sha=key.head_sha,
        config_hash=key.config_hash,
        pr_metadata_hash=key.pr_metadata_hash,
    )
    try:
        raw = client.get(cache_key)
        if raw is None:
            return None
        if not isinstance(raw, str):
            return None
        parsed: object = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    except redis.exceptions.RedisError as exc:
        logger.info("analysis result cache get failed (%s)", exc)
        return None
    finally:
        client.close()


def set_cached_final_report(
    settings: AppSettings,
    key: "AnalysisNaturalKey",
    report: dict[str, object],
) -> None:
    """Persist ``report`` JSON with the §13.6 TTL (best-effort; ignores Redis errors)."""

    client = connect_redis(settings)
    if client is None:
        return
    cache_key = analysis_cache_key(
        repository_id=key.repository_id,
        pull_number=key.pull_number,
        head_sha=key.head_sha,
        config_hash=key.config_hash,
        pr_metadata_hash=key.pr_metadata_hash,
    )
    try:
        payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
        client.set(cache_key, payload, ex=ANALYSIS_RESULT_CACHE_TTL_SECONDS)
    except (TypeError, ValueError):
        return
    except redis.exceptions.RedisError as exc:
        logger.warning("analysis result cache set failed (%s)", exc)
    finally:
        client.close()
