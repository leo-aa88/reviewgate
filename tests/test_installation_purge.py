"""Tests for installation-scoped analysis retention purge (GitHub #124)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytest.importorskip("dramatiq")
pytest.importorskip("sqlalchemy")

from reviewgate.app.analysis.jobs import purge_analyses_for_old_uninstalls
from reviewgate.app.storage.installation_purge import (
    purge_analyses_for_uninstalled_installations,
)
from reviewgate.app.storage.models import Analysis, AnalysisReport, Installation, Repository

_METADATA: Final[MetaData] = MetaData()


def _install_tables() -> None:
    """Register ORM tables once for the in-memory SQLite engine."""

    if not _METADATA.tables:
        Installation.__table__.to_metadata(_METADATA)
        Repository.__table__.to_metadata(_METADATA)
        Analysis.__table__.to_metadata(_METADATA)
        AnalysisReport.__table__.to_metadata(_METADATA)


@pytest.fixture
def purge_session() -> Session:
    """SQLite session with the tables ``purge_analyses_*`` touches."""

    _install_tables()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _METADATA.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_purge_analyses_for_uninstalled_installations_deletes_rows(
    purge_session: Session,
) -> None:
    """Rows tied to repositories whose installation was deleted long ago go away."""

    session = purge_session
    inst_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    old = datetime.now(UTC) - timedelta(days=31)
    session.add(
        Installation(
            id=inst_id,
            github_installation_id=9001,
            account_login="acme",
            account_type="Organization",
            created_at=datetime.now(UTC),
            deleted_at=old,
        ),
    )
    session.add(
        Repository(
            id=repo_id,
            installation_id=inst_id,
            github_repository_id=424242,
            owner="acme",
            name="demo",
            full_name="acme/demo",
            private=False,
            active=False,
            created_at=datetime.now(UTC),
        ),
    )
    session.flush()
    analysis_id = uuid.uuid4()
    session.add(
        Analysis(
            id=analysis_id,
            repository_id=repo_id,
            pull_number=1,
            head_sha="a" * 40,
            config_hash="c",
            pr_metadata_hash="m",
            status="completed",
            reviewability="PASS",
            created_at=datetime.now(UTC),
        ),
    )
    session.flush()
    report_id = uuid.uuid4()
    session.add(
        AnalysisReport(
            id=report_id,
            analysis_id=analysis_id,
            report_json={"reviewability": "PASS"},
            deterministic_json={"reviewability": "PASS"},
            created_at=datetime.now(UTC),
        ),
    )
    session.commit()

    r_del, a_del = purge_analyses_for_uninstalled_installations(session)
    assert r_del == 1
    assert a_del == 1
    session.commit()

    assert session.scalars(select(AnalysisReport)).all() == []
    assert session.scalars(select(Analysis)).all() == []


def test_purge_analyses_for_old_uninstalls_fn_noops_without_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Dramatiq actor exits quietly when ``REVIEWGATE_DATABASE_URL`` is unset."""

    monkeypatch.delenv("REVIEWGATE_DATABASE_URL", raising=False)
    purge_analyses_for_old_uninstalls.fn({})
