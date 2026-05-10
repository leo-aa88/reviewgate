"""Tests for :mod:`reviewgate.app.redis_client`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pydantic_settings")

from reviewgate.app.redis_client import connect_redis
from reviewgate.app.settings import AppSettings


def test_connect_redis_returns_none_without_url() -> None:
    """No Redis client is created when the URL is unset."""

    settings = AppSettings(redis_url=None)
    assert connect_redis(settings) is None


def test_connect_redis_uses_from_url_when_configured() -> None:
    """A configured URL delegates to ``redis.from_url``."""

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    fake_client = MagicMock()
    with patch(
        "reviewgate.app.redis_client.redis.from_url",
        return_value=fake_client,
    ) as from_url:
        client = connect_redis(settings)
    from_url.assert_called_once_with(
        "redis://127.0.0.1:6379/0",
        decode_responses=True,
    )
    assert client is fake_client
