"""Alembic migration environment for ReviewGate hosted-app persistence.

Loads SQLAlchemy metadata from :mod:`reviewgate.app.storage.models` and
configures the database URL from (in order of precedence):

1. Environment variable ``REVIEWGATE_DATABASE_URL`` (recommended for CI and
   local shells).
2. The ``sqlalchemy.url`` entry in ``alembic.ini``.

Example:
    Applying migrations against a local database::

        export REVIEWGATE_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/reviewgate
        alembic upgrade head
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from reviewgate.app.storage.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the synchronous SQLAlchemy URL for migrations."""

    env_url = os.environ.get("REVIEWGATE_DATABASE_URL", "").strip()
    if env_url:
        return env_url
    ini_url = config.get_main_option("sqlalchemy.url")
    if not ini_url:
        msg = (
            "Database URL is not configured: set REVIEWGATE_DATABASE_URL or "
            "sqlalchemy.url in alembic.ini"
        )
        raise RuntimeError(msg)
    return ini_url


def run_migrations_offline() -> None:
    """Render SQL to stdout without requiring a live DB connection."""

    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations inside a live database connection."""

    section = config.get_section(config.config_ini_section) or {}
    configuration = dict(section)
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
