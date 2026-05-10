"""Tests for Redis key helpers in :mod:`reviewgate.app.analysis.cache`."""

from __future__ import annotations

import uuid

from reviewgate.app.analysis.cache import analysis_cache_key, worker_job_lock_key


def test_analysis_cache_key_is_stable_and_namespaced() -> None:
    """Cache keys stay deterministic and distinct from lock keys."""

    rid = uuid.UUID("00000000-0000-4000-8000-000000000042")
    key = analysis_cache_key(
        repository_id=rid,
        pull_number=7,
        head_sha="deadbeef",
        config_hash="cfg9",
        pr_metadata_hash="meta9",
    )
    assert key == (
        "reviewgate:cache:analysis:v1:"
        "00000000-0000-4000-8000-000000000042:7:deadbeef:cfg9:meta9"
    )


def test_worker_job_lock_key_matches_design_composite() -> None:
    """Lock keys reuse the same composite segments as cache keys (§13.7)."""

    rid = uuid.UUID("00000000-0000-4000-8000-000000000042")
    lock_key = worker_job_lock_key(
        repository_id=rid,
        pull_number=7,
        head_sha="deadbeef",
        config_hash="cfg9",
        pr_metadata_hash="meta9",
    )
    cache_key = analysis_cache_key(
        repository_id=rid,
        pull_number=7,
        head_sha="deadbeef",
        config_hash="cfg9",
        pr_metadata_hash="meta9",
    )
    assert lock_key.startswith("reviewgate:lock:job:v1:")
    assert cache_key.startswith("reviewgate:cache:analysis:v1:")
    suffix = "00000000-0000-4000-8000-000000000042:7:deadbeef:cfg9:meta9"
    assert lock_key.endswith(suffix)
    assert cache_key.endswith(suffix)
