"""``run_pr_analysis_stub`` failure persistence (issue #50).

Covers ``mark_analysis_failed`` branches that must not regress without job-level
tests: missing host context, installation id drift vs Postgres, and non-retriable
GitHub REST failures.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("dramatiq")


def _memory_engine_and_factory(
    *,
    github_installation_id: int,
    github_repository_id: int,
) -> tuple[object, object, uuid.UUID]:
    """Build SQLite schema with one installation and repository; return ``(engine, factory, repo_uuid)``."""

    from sqlalchemy import MetaData, create_engine
    from sqlalchemy.orm import sessionmaker

    from reviewgate.app.storage.models import Analysis, Installation, Repository

    md = MetaData()
    Installation.__table__.to_metadata(md)
    Repository.__table__.to_metadata(md)
    Analysis.__table__.to_metadata(md)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    md.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    inst_id = uuid.uuid4()
    repo_uuid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    with factory() as setup_session:
        setup_session.add(
            Installation(
                id=inst_id,
                github_installation_id=github_installation_id,
                account_login="acme",
                account_type="Organization",
                created_at=now,
            ),
        )
        setup_session.add(
            Repository(
                id=repo_uuid,
                installation_id=inst_id,
                github_repository_id=github_repository_id,
                owner="acme",
                name="demo",
                full_name="acme/demo",
                private=False,
                active=True,
                created_at=now,
            ),
        )
        setup_session.commit()
    return engine, factory, repo_uuid


def _patch_job_db(
    monkeypatch: pytest.MonkeyPatch,
    engine: object,
    factory: object,
) -> None:
    """Point ``run_pr_analysis_stub`` at the in-memory engine."""

    import reviewgate.app.analysis.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "create_engine_from_settings", lambda _s: engine)
    monkeypatch.setattr(jobs_mod, "create_session_factory", lambda _e: factory)
    monkeypatch.setattr(jobs_mod, "get_cached_final_report", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_mod, "set_cached_final_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jobs_mod,
        "check_analysis_rate_limits",
        lambda *_a, **_k: "ok",
    )


def test_run_pr_analysis_stub_failed_missing_repository_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown ``reviewgate_repository_id`` yields ``missing_repository_context``."""

    import dramatiq
    from dramatiq.brokers.stub import StubBroker
    from sqlalchemy import select

    from reviewgate.app.storage.models import Analysis
    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    dramatiq.set_broker(StubBroker())
    engine, factory, _repo_uuid = _memory_engine_and_factory(
        github_installation_id=9510,
        github_repository_id=95910,
    )
    _patch_job_db(monkeypatch, engine, factory)
    orphan_repo_id = uuid.uuid4()

    run_pr_analysis_stub(
        {
            "github_installation_id": 9510,
            "github_repository_id": 95910,
            "reviewgate_repository_id": str(orphan_repo_id),
            "reviewgate_pull_number": 1,
            "reviewgate_head_sha": "sha1",
            "reviewgate_config_hash": "ch",
            "reviewgate_pr_metadata_hash": "mh",
        },
    )

    with factory() as verify:
        row = verify.execute(select(Analysis)).scalar_one()
        assert row.status == "failed"
        assert row.error_code == "missing_repository_context"


def test_run_pr_analysis_stub_failed_installation_context_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payload installation id must match Postgres for the repository row."""

    import dramatiq
    from dramatiq.brokers.stub import StubBroker
    from sqlalchemy import select

    from reviewgate.app.storage.models import Analysis
    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    dramatiq.set_broker(StubBroker())
    engine, factory, repo_uuid = _memory_engine_and_factory(
        github_installation_id=9620,
        github_repository_id=96920,
    )
    _patch_job_db(monkeypatch, engine, factory)

    run_pr_analysis_stub(
        {
            "github_installation_id": 9621,
            "github_repository_id": 96920,
            "reviewgate_repository_id": str(repo_uuid),
            "reviewgate_pull_number": 1,
            "reviewgate_head_sha": "sha1",
            "reviewgate_config_hash": "ch",
            "reviewgate_pr_metadata_hash": "mh",
        },
    )

    with factory() as verify:
        row = verify.execute(select(Analysis)).scalar_one()
        assert row.status == "failed"
        assert row.error_code == "installation_context_mismatch"


def test_run_pr_analysis_stub_failed_non_retriable_github_rest_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-retriable :class:`~reviewgate.app.github.client.GitHubRestError`` marks ``github_rest``."""

    import dramatiq
    from dramatiq.brokers.stub import StubBroker
    from sqlalchemy import select

    from reviewgate.app.github.client import GitHubRestError
    from reviewgate.app.storage.models import Analysis
    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    dramatiq.set_broker(StubBroker())
    engine, factory, repo_uuid = _memory_engine_and_factory(
        github_installation_id=9730,
        github_repository_id=97930,
    )
    _patch_job_db(monkeypatch, engine, factory)

    import reviewgate.app.analysis.jobs as jobs_mod

    def _boom(*_a: object, **_k: object) -> object:
        raise GitHubRestError(
            "not found",
            status_code=404,
            retriable=False,
            request_id="abc",
        )

    monkeypatch.setattr(jobs_mod, "run_pr_analysis_for_natural_key", _boom)

    run_pr_analysis_stub(
        {
            "github_installation_id": 9730,
            "github_repository_id": 97930,
            "reviewgate_repository_id": str(repo_uuid),
            "reviewgate_pull_number": 1,
            "reviewgate_head_sha": "sha1",
            "reviewgate_config_hash": "ch",
            "reviewgate_pr_metadata_hash": "mh",
        },
    )

    with factory() as verify:
        row = verify.execute(select(Analysis)).scalar_one()
        assert row.status == "failed"
        assert row.error_code == "github_rest"
