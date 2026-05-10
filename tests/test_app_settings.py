"""Tests for :mod:`reviewgate.app.settings`."""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic_settings")

from reviewgate.app.settings import AppSettings


def test_app_settings_reads_reviewgate_redis_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``REVIEWGATE_REDIS_URL`` populates :attr:`AppSettings.redis_url`."""

    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/2")
    settings = AppSettings()
    assert settings.redis_url == "redis://127.0.0.1:6379/2"


def test_app_settings_redis_url_defaults_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without env configuration Redis stays disabled."""

    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    settings = AppSettings()
    assert settings.redis_url is None
