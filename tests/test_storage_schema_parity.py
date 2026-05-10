"""Post-migration schema parity checks for ``reviewgate.app.storage`` (§16.1).

After ``alembic upgrade head`` applies the revision against a PostgreSQL
database, :func:`alembic.autogenerate.compare_metadata` must report **no drift**
between the live catalog and :class:`sqlalchemy.schema.MetaData` built from the
ORM. This catches accidental divergence between ``models.py`` and the Alembic
revision.

These tests are skipped unless ``REVIEWGATE_DATABASE_URL`` points at the
database that was just migrated (CI ``alembic-smoke`` job sets this).
"""

from __future__ import annotations

import os
from typing import Final

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

pytest.importorskip("sqlalchemy")

from reviewgate.app.storage.models import Base

_SKIP_REASON: Final[str] = (
    "Set REVIEWGATE_DATABASE_URL to a PostgreSQL database after "
    "`alembic upgrade head` to run schema parity checks."
)


@pytest.mark.skipif(
    not os.environ.get("REVIEWGATE_DATABASE_URL", "").strip(),
    reason=_SKIP_REASON,
)
def test_migrated_database_matches_orm_metadata() -> None:
    """ORM metadata and the migrated database catalog must be equivalent."""

    url = os.environ["REVIEWGATE_DATABASE_URL"].strip()
    engine = create_engine(url)
    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "compare_server_default": True,
            },
        )
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], (
        "Alembic autogenerate detected drift between ORM metadata and the "
        f"database (apply models.py and revision changes together): {diff!r}"
    )
