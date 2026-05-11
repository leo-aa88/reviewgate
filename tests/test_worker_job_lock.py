"""Tests for :mod:`reviewgate.app.analysis.worker_job_lock` (issue #47)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from reviewgate.app.analysis.worker_job_lock import worker_job_lock_hold
from reviewgate.app.storage.repositories import AnalysisNaturalKey


def _sample_key() -> AnalysisNaturalKey:
    return AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=1,
        head_sha="abc",
        config_hash="cfg",
        pr_metadata_hash="meta",
    )


def test_worker_job_lock_hold_skips_redis_when_url_missing() -> None:
    """Without ``redis_url`` the context is a no-op success."""

    with worker_job_lock_hold(None, _sample_key()) as ok:
        assert ok


def test_worker_job_lock_hold_acquires_releases_on_success() -> None:
    """``SET NX`` success yields ``True`` and runs compare-and-delete release."""

    key = _sample_key()
    client = MagicMock()
    client.set.return_value = True
    client.eval.return_value = 1

    with patch("redis.Redis.from_url", return_value=client):
        with worker_job_lock_hold("redis://127.0.0.1:6379/0", key) as ok:
            assert ok is True

    client.set.assert_called_once()
    client.eval.assert_called_once()
    client.close.assert_called_once()


def test_worker_job_lock_hold_yields_false_when_not_acquired() -> None:
    """Contention yields ``False`` without ``EVAL`` cleanup."""

    key = _sample_key()
    client = MagicMock()
    client.set.return_value = None

    with patch("redis.Redis.from_url", return_value=client):
        with worker_job_lock_hold("redis://127.0.0.1:6379/0", key) as ok:
            assert ok is False

    client.eval.assert_not_called()
    client.close.assert_called_once()
