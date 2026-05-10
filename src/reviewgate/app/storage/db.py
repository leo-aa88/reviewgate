"""SQLAlchemy engine and session factory helpers (``docs/DESIGN.md`` §15 / §16).

The hosted app uses Postgres for persistence (§16.1). This module exposes small
factory functions so routes and workers obtain a :class:`~sqlalchemy.orm.Session`
without duplicating engine configuration. When ``database_url`` is unset in
:class:`~reviewgate.app.settings.AppSettings`, factories return ``None`` so the
API process can still boot for health checks before infrastructure is wired.

Example:
    Creating a session scope when a database URL is configured::

        from reviewgate.app.settings import AppSettings
        from reviewgate.app.storage.db import create_engine_from_settings
        from reviewgate.app.storage.db import create_session_factory

        settings = AppSettings()
        engine = create_engine_from_settings(settings)
        if engine is not None:
            SessionLocal = create_session_factory(engine)
            with SessionLocal() as session:
                pass
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from reviewgate.app.settings import AppSettings


def create_engine_from_settings(settings: AppSettings) -> Engine | None:
    """Return a synchronous SQLAlchemy engine or ``None`` if no URL is set."""

    if settings.database_url is None:
        return None
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a :class:`sessionmaker` bound to ``engine``."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
