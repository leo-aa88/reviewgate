"""Tests for :mod:`reviewgate.app.rate_limit.limiter` (issue #49)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("redis")
import redis.exceptions

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
    client.incr.side_effect = [501]
    client.decr = MagicMock()
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
    client.decr.assert_called_once()


def test_repository_cap_rolls_back_installation_increment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repository over cap must not leave a leaked installation increment (PR #114)."""

    client = MagicMock()
    client.incr.side_effect = [1, 101]
    client.decr = MagicMock()
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
    client.decr.assert_called_once()
    inst_key = client.incr.call_args_list[0][0][0]
    assert client.decr.call_args[0][0] == inst_key


def test_repository_incr_redis_error_rolls_back_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``INCR`` on the repository key fails, undo the installation increment."""

    client = MagicMock()
    client.incr.side_effect = [1, redis.exceptions.ConnectionError("redis down")]
    client.decr = MagicMock()
    client.close = MagicMock()

    monkeypatch.setattr(
        "reviewgate.app.rate_limit.limiter.connect_redis",
        lambda _s: client,
    )

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    assert (
        check_analysis_rate_limits(
            settings,
            github_installation_id=11,
            github_repository_id=12,
        )
        == "ok"
    )
    assert client.incr.call_count == 2
    client.decr.assert_called_once()


def test_repository_incr_invalid_value_rolls_back_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-integer repository counter response rolls back the installation bump."""

    client = MagicMock()
    client.incr.side_effect = [1, "not-an-int"]
    client.decr = MagicMock()
    client.close = MagicMock()

    monkeypatch.setattr(
        "reviewgate.app.rate_limit.limiter.connect_redis",
        lambda _s: client,
    )

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    assert (
        check_analysis_rate_limits(
            settings,
            github_installation_id=13,
            github_repository_id=14,
        )
        == "ok"
    )
    client.decr.assert_called_once()
