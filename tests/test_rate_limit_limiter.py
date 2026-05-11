"""Tests for :mod:`reviewgate.app.rate_limit.limiter` (issue #49)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("redis")

from reviewgate.app.rate_limit.limiter import check_analysis_rate_limits
from reviewgate.app.settings import AppSettings


def test_check_analysis_rate_limits_ok_without_redis_url() -> None:
    """No Redis URL means counters are skipped (fail-open)."""

    settings = AppSettings(redis_url=None)
    assert (
        check_analysis_rate_limits(
            settings,
            github_installation_id=1,
            github_repository_id=2,
        )
        == "ok"
    )


def test_check_analysis_rate_limits_ok_for_non_positive_ids() -> None:
    """Malformed ids must not touch Redis."""

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    assert (
        check_analysis_rate_limits(
            settings,
            github_installation_id=0,
            github_repository_id=1,
        )
        == "ok"
    )


def test_installation_cap_returns_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installation counter over §22.2 default yields ``installation_exceeded``."""

    client = MagicMock()
    client.incr.side_effect = [501, 1]
    client.expire = MagicMock()
    client.close = MagicMock()

    monkeypatch.setattr(
        "reviewgate.app.rate_limit.limiter.connect_redis",
        lambda _s: client,
    )

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    assert (
        check_analysis_rate_limits(
            settings,
            github_installation_id=7,
            github_repository_id=8,
        )
        == "installation_exceeded"
    )
    client.incr.assert_called_once()


def test_repository_cap_after_installation_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repository counter is evaluated only when installation counter passes."""

    client = MagicMock()
    client.incr.side_effect = [1, 101]
    client.expire = MagicMock()
    client.close = MagicMock()

    monkeypatch.setattr(
        "reviewgate.app.rate_limit.limiter.connect_redis",
        lambda _s: client,
    )

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    assert (
        check_analysis_rate_limits(
            settings,
            github_installation_id=9,
            github_repository_id=10,
        )
        == "repository_exceeded"
    )
    assert client.incr.call_count == 2
