"""Tests for :mod:`reviewgate.app.storage.db`."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory


def test_create_engine_from_settings_returns_none_without_url() -> None:
    """No engine is created when ``database_url`` is unset."""

    settings = AppSettings(database_url=None)
    assert create_engine_from_settings(settings) is None


def test_create_session_factory_roundtrip() -> None:
    """Session factory binds to the provided in-memory SQLite engine."""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    factory = create_session_factory(engine)
    session = factory()
    try:
        assert isinstance(session, Session)
    finally:
        session.close()
        engine.dispose()
