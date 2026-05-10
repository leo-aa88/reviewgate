"""Regression tests for ``reviewgate.app.storage`` ORM schema (§16.1).

These tests validate that SQLAlchemy :class:`~sqlalchemy.schema.MetaData`
matches ``docs/DESIGN.md`` §16.1 (table names, composite uniqueness, and
indexes). They require optional ``app`` dependencies (installed via
``pip install -e ".[dev,app]"`` in CI).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from sqlalchemy.schema import UniqueConstraint

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from alembic.config import Config
from alembic.script import ScriptDirectory

from reviewgate.app.storage import models
from reviewgate.app.storage.models import Base

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_ALEMBIC_INI: Final[Path] = _REPO_ROOT / "alembic.ini"
_EXPECTED_ALEMBIC_HEAD: Final[str] = "16_1_0001"

_EXPECTED_TABLES: Final[frozenset[str]] = frozenset(
    {
        models.TABLE_INSTALLATIONS,
        models.TABLE_REPOSITORIES,
        models.TABLE_ANALYSES,
        models.TABLE_ANALYSIS_REPORTS,
        models.TABLE_BETA_LEADS,
        models.TABLE_WEBHOOK_DELIVERIES,
    },
)

_ANALYSES_NATURAL_KEY_COLUMNS: Final[tuple[str, ...]] = (
    "repository_id",
    "pull_number",
    "head_sha",
    "config_hash",
    "pr_metadata_hash",
)


def test_section_16_1_tables_registered() -> None:
    """Every §16.1 logical table maps to ORM metadata."""

    assert set(Base.metadata.tables) == _EXPECTED_TABLES


def test_analyses_composite_unique_constraint() -> None:
    """``analyses`` carries the five-column uniqueness rule from §16.1."""

    table = Base.metadata.tables[models.TABLE_ANALYSES]
    composite = next(
        c
        for c in table.constraints
        if isinstance(c, UniqueConstraint) and c.name == models.UQ_ANALYSES_NATURAL_KEY
    )
    assert tuple(col.name for col in composite.columns) == _ANALYSES_NATURAL_KEY_COLUMNS


def test_analyses_and_webhook_indexes() -> None:
    """Index names match the design document."""

    analyses = Base.metadata.tables[models.TABLE_ANALYSES]
    index_names = {idx.name for idx in analyses.indexes}
    assert models.INDEX_ANALYSES_REPO_PR in index_names
    assert models.INDEX_ANALYSES_CREATED_AT in index_names

    deliveries = Base.metadata.tables[models.TABLE_WEBHOOK_DELIVERIES]
    delivery_indexes = {idx.name for idx in deliveries.indexes}
    assert models.INDEX_WEBHOOK_DELIVERIES_CREATED_AT in delivery_indexes


def test_webhook_delivery_id_is_unique() -> None:
    """``github_delivery_id`` is unique for dedupe (§16.1 ``webhook_deliveries``)."""

    table = Base.metadata.tables[models.TABLE_WEBHOOK_DELIVERIES]
    col = table.c.github_delivery_id
    assert col.unique is True


def test_installation_and_repository_numeric_ids_unique() -> None:
    """GitHub numeric ids are unique per §16.1."""

    inst = Base.metadata.tables[models.TABLE_INSTALLATIONS]
    assert inst.c.github_installation_id.unique is True
    repos = Base.metadata.tables[models.TABLE_REPOSITORIES]
    assert repos.c.github_repository_id.unique is True


def test_alembic_head_revision_is_registered() -> None:
    """The initial migration revision is registered in the Alembic script graph."""

    assert _ALEMBIC_INI.is_file(), f"missing Alembic config: {_ALEMBIC_INI}"
    cfg = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert _EXPECTED_ALEMBIC_HEAD in heads, (
        f"expected Alembic head {_EXPECTED_ALEMBIC_HEAD!r} in {heads!r}"
    )
