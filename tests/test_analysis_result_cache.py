"""Tests for :mod:`reviewgate.app.analysis.result_cache` (issue #48)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest

pytest.importorskip("redis")

from reviewgate.app.analysis.cache import analysis_cache_key
from reviewgate.app.analysis.result_cache import (
    ANALYSIS_RESULT_CACHE_TTL_SECONDS,
    get_cached_final_report,
    set_cached_final_report,
)
from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.repositories import AnalysisNaturalKey


def test_analysis_result_cache_ttl_is_24h() -> None:
    """§13.6 documents a one-day retention window."""

    assert ANALYSIS_RESULT_CACHE_TTL_SECONDS == 86400


def test_get_cached_final_report_returns_none_when_redis_unconfigured() -> None:
    """Without ``REVIEWGATE_REDIS_URL`` there is no cache client."""

    settings = AppSettings(redis_url=None)
    key = AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=1,
        head_sha="a",
        config_hash="c",
        pr_metadata_hash="m",
    )
    assert get_cached_final_report(settings, key) is None


def test_get_and_set_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache uses :func:`analysis_cache_key` and JSON payloads."""

    key = AnalysisNaturalKey(
        repository_id=uuid.UUID("00000000-0000-4000-8000-0000000000aa"),
        pull_number=2,
        head_sha="deadbeef",
        config_hash="cfg",
        pr_metadata_hash="meta",
    )
    client = MagicMock()
    client.get.return_value = json.dumps({"ok": True})
    client.close = MagicMock()

    monkeypatch.setattr(
        "reviewgate.app.analysis.result_cache.connect_redis",
        lambda _s: client,
    )

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    assert get_cached_final_report(settings, key) == {"ok": True}
    expected_key = analysis_cache_key(
        repository_id=key.repository_id,
        pull_number=key.pull_number,
        head_sha=key.head_sha,
        config_hash=key.config_hash,
        pr_metadata_hash=key.pr_metadata_hash,
    )
    client.get.assert_called_once_with(expected_key)

    client2 = MagicMock()
    client2.close = MagicMock()
    monkeypatch.setattr(
        "reviewgate.app.analysis.result_cache.connect_redis",
        lambda _s: client2,
    )
    set_cached_final_report(settings, key, {"x": 1})
    client2.set.assert_called_once()
    set_args, set_kwargs = client2.set.call_args
    assert set_args[0] == expected_key
    assert json.loads(set_args[1]) == {"x": 1}
    assert set_kwargs.get("ex") == ANALYSIS_RESULT_CACHE_TTL_SECONDS
