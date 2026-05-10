"""Tests for :mod:`reviewgate.app.storage.repositories` (issue #46)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker

pytest.importorskip("sqlalchemy")

from reviewgate.app.storage.models import Analysis, Installation, Repository
from reviewgate.app.storage.repositories import (
    ANALYSIS_STATUS_COMPLETED,
    ANALYSIS_STATUS_FAILED,
    ANALYSIS_STATUS_RUNNING,
    AnalysisNaturalKey,
    begin_analysis_for_job_start,
    mark_analysis_completed,
    mark_analysis_failed,
    parse_analysis_job_natural_key,
)


def _subset_metadata() -> MetaData:
    """SQLite-friendly DDL for FK tables referenced by ``analyses``."""

    md = MetaData()
    Installation.__table__.to_metadata(md)
    Repository.__table__.to_metadata(md)
    Analysis.__table__.to_metadata(md)
    return md


@pytest.fixture
def repo_session() -> Session:
    """In-memory SQLite session with §16.1 subset tables."""

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _subset_metadata().create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_installation_and_repository(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    inst_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    session.add(
        Installation(
            id=inst_id,
            github_installation_id=9001,
            account_login="acme",
            account_type="Organization",
            created_at=now,
        ),
    )
    session.add(
        Repository(
            id=repo_id,
            installation_id=inst_id,
            github_repository_id=42,
            owner="acme",
            name="demo",
            full_name="acme/demo",
            private=False,
            active=True,
            created_at=now,
        ),
    )
    session.commit()
    return inst_id, repo_id


def test_parse_analysis_job_natural_key_accepts_uuid_object() -> None:
    """UUID values may be passed as :class:`uuid.UUID` instances."""

    rid = uuid.uuid4()
    payload: dict[str, object] = {
        "reviewgate_repository_id": rid,
        "reviewgate_pull_number": 3,
        "reviewgate_head_sha": "abc",
        "reviewgate_config_hash": "cfg",
        "reviewgate_pr_metadata_hash": "meta",
    }
    key = parse_analysis_job_natural_key(payload)
    assert key is not None
    assert key.repository_id == rid


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"reviewgate_repository_id": str(uuid.uuid4())},
        {
            "reviewgate_repository_id": str(uuid.uuid4()),
            "reviewgate_pull_number": 0,
            "reviewgate_head_sha": "a",
            "reviewgate_config_hash": "c",
            "reviewgate_pr_metadata_hash": "m",
        },
        {
            "reviewgate_repository_id": "not-a-uuid",
            "reviewgate_pull_number": 1,
            "reviewgate_head_sha": "a",
            "reviewgate_config_hash": "c",
            "reviewgate_pr_metadata_hash": "m",
        },
    ],
)
def test_parse_analysis_job_natural_key_returns_none(payload: dict[str, object]) -> None:
    """Partial or invalid payloads are ignored."""

    assert parse_analysis_job_natural_key(payload) is None


def test_begin_analysis_inserts_running_row(repo_session: Session) -> None:
    """First worker creates a ``running`` analysis."""

    _inst, repo_id = _seed_installation_and_repository(repo_session)
    key = AnalysisNaturalKey(
        repository_id=repo_id,
        pull_number=7,
        head_sha="deadbeef",
        config_hash="c1",
        pr_metadata_hash="m1",
    )
    aid, kind = begin_analysis_for_job_start(repo_session, key)
    assert kind == "created"
    row = repo_session.get(Analysis, aid)
    assert row is not None
    assert row.status == ANALYSIS_STATUS_RUNNING
    repo_session.commit()


def test_begin_analysis_completed_short_circuits(repo_session: Session) -> None:
    """Second job for the same natural key sees ``already_completed``."""

    _inst, repo_id = _seed_installation_and_repository(repo_session)
    key = AnalysisNaturalKey(
        repository_id=repo_id,
        pull_number=2,
        head_sha="aa",
        config_hash="c",
        pr_metadata_hash="m",
    )
    aid, _ = begin_analysis_for_job_start(repo_session, key)
    mark_analysis_completed(repo_session, aid, reviewability="PASS")
    repo_session.commit()

    aid2, kind = begin_analysis_for_job_start(repo_session, key)
    assert aid2 == aid
    assert kind == "already_completed"


def test_begin_analysis_resumes_failed(repo_session: Session) -> None:
    """Failed rows reset to ``running`` for a retry."""

    _inst, repo_id = _seed_installation_and_repository(repo_session)
    key = AnalysisNaturalKey(
        repository_id=repo_id,
        pull_number=5,
        head_sha="bb",
        config_hash="c",
        pr_metadata_hash="m",
    )
    aid, _ = begin_analysis_for_job_start(repo_session, key)
    mark_analysis_failed(repo_session, aid, error_code="boom")
    repo_session.commit()

    aid2, kind = begin_analysis_for_job_start(repo_session, key)
    assert aid2 == aid
    assert kind == "resumed_from_failed"
    row = repo_session.get(Analysis, aid2)
    assert row is not None
    assert row.status == ANALYSIS_STATUS_RUNNING
    assert row.error_code is None


def test_begin_analysis_second_running_worker(repo_session: Session) -> None:
    """Concurrent ``running`` rows surface as ``already_running``."""

    _inst, repo_id = _seed_installation_and_repository(repo_session)
    key = AnalysisNaturalKey(
        repository_id=repo_id,
        pull_number=9,
        head_sha="cc",
        config_hash="c",
        pr_metadata_hash="m",
    )
    aid, k1 = begin_analysis_for_job_start(repo_session, key)
    assert k1 == "created"
    repo_session.commit()

    aid2, k2 = begin_analysis_for_job_start(repo_session, key)
    assert aid2 == aid
    assert k2 == "already_running"


_UNIQUE_VIOLATION: Final[str] = (
    "unique constraint should reject duplicate natural keys at flush time"
)


def test_analyses_unique_constraint_enforced(repo_session: Session) -> None:
    """Database rejects two inserts with the same five-part key."""

    from sqlalchemy.exc import IntegrityError

    _inst, repo_id = _seed_installation_and_repository(repo_session)
    key = AnalysisNaturalKey(
        repository_id=repo_id,
        pull_number=1,
        head_sha="dd",
        config_hash="c",
        pr_metadata_hash="m",
    )
    begin_analysis_for_job_start(repo_session, key)
    repo_session.commit()

    dup = Analysis(
        id=uuid.uuid4(),
        repository_id=key.repository_id,
        pull_number=key.pull_number,
        head_sha=key.head_sha,
        config_hash=key.config_hash,
        pr_metadata_hash=key.pr_metadata_hash,
        status=ANALYSIS_STATUS_RUNNING,
        created_at=datetime.now(tz=UTC),
    )
    repo_session.add(dup)
    with pytest.raises(IntegrityError):
        repo_session.flush()
