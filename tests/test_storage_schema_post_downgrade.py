"""Verify ``alembic downgrade base`` removes §16.1 tables.

Run after ``alembic downgrade base`` in CI (see ``alembic-smoke`` job). Ensures
the initial revision is reversible and does not leave hosted-app tables behind.

Requires ``REVIEWGATE_DATABASE_URL`` (same disposable Postgres instance as the
parity tests).
"""

from __future__ import annotations

import os
from typing import Final

import pytest
from sqlalchemy import create_engine, inspect

pytest.importorskip("sqlalchemy")

from reviewgate.app.storage import models

_SKIP_REASON: Final[str] = (
    "Set REVIEWGATE_DATABASE_URL after `alembic downgrade base` to assert "
    "tables were dropped."
)

_EXPECTED_ABSENT: Final[frozenset[str]] = frozenset(
    {
        models.TABLE_INSTALLATIONS,
        models.TABLE_REPOSITORIES,
        models.TABLE_ANALYSES,
        models.TABLE_ANALYSIS_REPORTS,
        models.TABLE_BETA_LEADS,
        models.TABLE_WEBHOOK_DELIVERIES,
    },
)


@pytest.mark.skipif(
    not os.environ.get("REVIEWGATE_DATABASE_URL", "").strip(),
    reason=_SKIP_REASON,
)
def test_downgrade_removed_section_16_1_tables() -> None:
    """No §16.1 application tables remain in ``public`` after ``downgrade base``."""

    url = os.environ["REVIEWGATE_DATABASE_URL"].strip()
    engine = create_engine(url)
    inspector = inspect(engine)
    present = frozenset(inspector.get_table_names(schema="public"))
    leftover = sorted(_EXPECTED_ABSENT & present)
    assert not leftover, (
        "Expected Alembic downgrade to drop §16.1 tables, but these remain: "
        f"{leftover!r}"
    )
