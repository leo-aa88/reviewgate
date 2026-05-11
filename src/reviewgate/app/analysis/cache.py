"""Redis key naming for analysis result cache and worker locks (§13.6–§13.7).

``docs/DESIGN.md`` §13.6 defines the **analysis cache** identity as the tuple:

    ``repository_id``, ``pull_number``, ``head_sha``, ``config_hash``,
    ``pr_metadata_hash``

Section §13.7 reuses the same logical tuple for the **worker job lock** so
enqueue dedupe, cache hits, and mutual exclusion all refer to one composite
analysis key.

Key layout (versioned, colon-separated)::

    reviewgate:<area>:v1:<repository_uuid>:<pull_number>:<head_sha>:
        <config_hash>:<pr_metadata_hash>

Where:

* ``<area>`` is ``cache:analysis`` for final cached JSON blobs (§13.6 TTL;
  see :mod:`reviewgate.app.analysis.result_cache`) or ``lock:job`` for Redis
  locks held during a
  single analysis run (§13.7 mechanism #3; wiring in issue #47).
* ``v1`` bumps if we ever need incompatible key layouts while sharing one Redis.
* Segments use ``str(repository_id)`` (lowercase UUID), decimal ``pull_number``,
  and raw Git/hash strings (hex SHA and application-defined hashes must not
  embed colon characters).

Example:
    Building keys for the same PR head and metadata::

        import uuid

        rid = uuid.UUID("00000000-0000-4000-8000-000000000001")
        ck = analysis_cache_key(
            repository_id=rid,
            pull_number=42,
            head_sha="abc123",
            config_hash="cfg",
            pr_metadata_hash="meta",
        )
        lk = worker_job_lock_key(
            repository_id=rid,
            pull_number=42,
            head_sha="abc123",
            config_hash="cfg",
            pr_metadata_hash="meta",
        )
"""

from __future__ import annotations

import uuid
from typing import Final

_KEY_VERSION: Final[str] = "v1"
_NAMESPACE: Final[str] = "reviewgate"
_SEGMENT_SEPARATOR: Final[str] = ":"

_AREA_CACHE_ANALYSIS: Final[str] = "cache:analysis"
_AREA_LOCK_JOB: Final[str] = "lock:job"


def _join_composite_key(
    area: str,
    *,
    repository_id: uuid.UUID,
    pull_number: int,
    head_sha: str,
    config_hash: str,
    pr_metadata_hash: str,
) -> str:
    """Build a colon-delimited Redis key under the shared ``v1`` namespace."""

    segments: tuple[str, ...] = (
        _NAMESPACE,
        area,
        _KEY_VERSION,
        str(repository_id),
        str(pull_number),
        head_sha,
        config_hash,
        pr_metadata_hash,
    )
    return _SEGMENT_SEPARATOR.join(segments)


def analysis_cache_key(
    *,
    repository_id: uuid.UUID,
    pull_number: int,
    head_sha: str,
    config_hash: str,
    pr_metadata_hash: str,
) -> str:
    """Return the Redis key for cached **final** analysis results (§13.6).

    Args:
        repository_id: Internal repository UUID (``repositories.id``).
        pull_number: GitHub pull request number.
        head_sha: PR head commit SHA at analysis time.
        config_hash: Effective ``.reviewgate.yml`` hash (or default sentinel).
        pr_metadata_hash: Normalized PR title/body/issue hash (§13.6).

    Returns:
        Namespaced Redis string suitable for ``SET``/``GET`` with a TTL.
    """

    return _join_composite_key(
        _AREA_CACHE_ANALYSIS,
        repository_id=repository_id,
        pull_number=pull_number,
        head_sha=head_sha,
        config_hash=config_hash,
        pr_metadata_hash=pr_metadata_hash,
    )


def worker_job_lock_key(
    *,
    repository_id: uuid.UUID,
    pull_number: int,
    head_sha: str,
    config_hash: str,
    pr_metadata_hash: str,
) -> str:
    """Return the Redis key used as a mutex for one analysis job (§13.7).

    Args:
        repository_id: Internal repository UUID (``repositories.id``).
        pull_number: GitHub pull request number.
        head_sha: PR head commit SHA at analysis time.
        config_hash: Effective ``.reviewgate.yml`` hash (or default sentinel).
        pr_metadata_hash: Normalized PR title/body/issue hash (§13.6).

    Returns:
        Namespaced Redis string suitable for ``SET key NX EX ...`` lock patterns.
    """

    return _join_composite_key(
        _AREA_LOCK_JOB,
        repository_id=repository_id,
        pull_number=pull_number,
        head_sha=head_sha,
        config_hash=config_hash,
        pr_metadata_hash=pr_metadata_hash,
    )
