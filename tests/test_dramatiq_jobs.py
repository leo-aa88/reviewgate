"""Tests for Dramatiq actors (``reviewgate.app.analysis.jobs``)."""

from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Final
from unittest.mock import MagicMock

import pytest

pytest.importorskip("dramatiq")

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent


def test_run_pr_analysis_stub_invokes_with_stub_broker() -> None:
    """Actors register against a :class:`~dramatiq.brokers.stub.StubBroker`."""

    import dramatiq
    from dramatiq.brokers.stub import StubBroker

    dramatiq.set_broker(StubBroker())
    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub({"pull_number": 1})


def test_run_pr_analysis_stub_marks_analysis_completed_with_natural_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional ``reviewgate_*`` keys persist ``analyses`` lifecycle (issue #46)."""

    import uuid
    from datetime import UTC, datetime

    import dramatiq
    from dramatiq.brokers.stub import StubBroker
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from reviewgate.app.storage.models import Analysis, Installation, Repository

    dramatiq.set_broker(StubBroker())

    def _subset_metadata() -> object:
        from sqlalchemy import MetaData

        md = MetaData()
        Installation.__table__.to_metadata(md)
        Repository.__table__.to_metadata(md)
        Analysis.__table__.to_metadata(md)
        return md

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _subset_metadata().create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    inst_id = uuid.uuid4()
    repo_uuid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    with factory() as setup_session:
        setup_session.add(
            Installation(
                id=inst_id,
                github_installation_id=9001,
                account_login="acme",
                account_type="Organization",
                created_at=now,
            ),
        )
        setup_session.add(
            Repository(
                id=repo_uuid,
                installation_id=inst_id,
                github_repository_id=4242,
                owner="acme",
                name="demo",
                full_name="acme/demo",
                private=False,
                active=True,
                created_at=now,
            ),
        )
        setup_session.commit()

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

    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub(
        {
            "github_installation_id": 9001,
            "github_repository_id": 4242,
            "reviewgate_repository_id": str(repo_uuid),
            "reviewgate_pull_number": 1,
            "reviewgate_head_sha": "sha1",
            "reviewgate_config_hash": "ch",
            "reviewgate_pr_metadata_hash": "mh",
        },
    )

    with factory() as verify:
        row = verify.execute(
            select(Analysis).where(Analysis.repository_id == repo_uuid),
        ).scalar_one()
        assert row.status == "completed"
        assert row.reviewability == "PASS"


def test_run_pr_analysis_stub_cache_hit_skips_db_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #48: a cache hit returns without mutating ``analyses``."""

    import uuid
    from contextlib import nullcontext
    from datetime import UTC, datetime

    import dramatiq
    from dramatiq.brokers.stub import StubBroker
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from reviewgate.app.storage.models import Analysis, Installation, Repository

    dramatiq.set_broker(StubBroker())

    def _subset_metadata() -> object:
        from sqlalchemy import MetaData

        md = MetaData()
        Installation.__table__.to_metadata(md)
        Repository.__table__.to_metadata(md)
        Analysis.__table__.to_metadata(md)
        return md

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _subset_metadata().create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    inst_id = uuid.uuid4()
    repo_uuid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    with factory() as setup_session:
        setup_session.add(
            Installation(
                id=inst_id,
                github_installation_id=9200,
                account_login="acme",
                account_type="Organization",
                created_at=now,
            ),
        )
        setup_session.add(
            Repository(
                id=repo_uuid,
                installation_id=inst_id,
                github_repository_id=9292,
                owner="acme",
                name="demo",
                full_name="acme/demo",
                private=False,
                active=True,
                created_at=now,
            ),
        )
        setup_session.commit()

    import reviewgate.app.analysis.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "create_engine_from_settings", lambda _s: engine)
    monkeypatch.setattr(jobs_mod, "create_session_factory", lambda _e: factory)
    monkeypatch.setattr(
        jobs_mod,
        "get_cached_final_report",
        lambda *_a, **_k: {"reviewability": "PASS", "cached": True},
    )
    cache_setter = MagicMock()
    monkeypatch.setattr(jobs_mod, "set_cached_final_report", cache_setter)
    monkeypatch.setattr(jobs_mod, "worker_job_lock_hold", lambda *_a, **_k: nullcontext(True))
    monkeypatch.setattr(
        jobs_mod,
        "check_analysis_rate_limits",
        lambda *_a, **_k: "ok",
    )
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")

    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub(
        {
            "github_installation_id": 9200,
            "github_repository_id": 9292,
            "reviewgate_repository_id": str(repo_uuid),
            "reviewgate_pull_number": 1,
            "reviewgate_head_sha": "sha1",
            "reviewgate_config_hash": "ch",
            "reviewgate_pr_metadata_hash": "mh",
        },
    )

    with factory() as verify:
        rows = verify.execute(select(Analysis)).scalars().all()
        assert rows == []
    cache_setter.assert_not_called()


def test_run_pr_analysis_stub_skips_db_when_worker_lock_not_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #47: losing the Redis worker lock exits before Postgres lifecycle."""

    import uuid
    from contextlib import contextmanager
    from datetime import UTC, datetime

    import dramatiq
    from dramatiq.brokers.stub import StubBroker
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from reviewgate.app.storage.models import Analysis, Installation, Repository

    dramatiq.set_broker(StubBroker())

    def _subset_metadata() -> object:
        from sqlalchemy import MetaData

        md = MetaData()
        Installation.__table__.to_metadata(md)
        Repository.__table__.to_metadata(md)
        Analysis.__table__.to_metadata(md)
        return md

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _subset_metadata().create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    inst_id = uuid.uuid4()
    repo_uuid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    with factory() as setup_session:
        setup_session.add(
            Installation(
                id=inst_id,
                github_installation_id=9100,
                account_login="acme",
                account_type="Organization",
                created_at=now,
            ),
        )
        setup_session.add(
            Repository(
                id=repo_uuid,
                installation_id=inst_id,
                github_repository_id=9191,
                owner="acme",
                name="demo",
                full_name="acme/demo",
                private=False,
                active=True,
                created_at=now,
            ),
        )
        setup_session.commit()

    import reviewgate.app.analysis.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "create_engine_from_settings", lambda _s: engine)
    monkeypatch.setattr(jobs_mod, "create_session_factory", lambda _e: factory)
    monkeypatch.setattr(jobs_mod, "get_cached_final_report", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_mod, "set_cached_final_report", lambda *_a, **_k: None)

    @contextmanager
    def _deny_lock(*_a: object, **_k: object) -> object:
        yield False

    monkeypatch.setattr(jobs_mod, "worker_job_lock_hold", _deny_lock)
    monkeypatch.setattr(
        jobs_mod,
        "check_analysis_rate_limits",
        lambda *_a, **_k: "ok",
    )
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")

    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub(
        {
            "github_installation_id": 9100,
            "github_repository_id": 9191,
            "reviewgate_repository_id": str(repo_uuid),
            "reviewgate_pull_number": 1,
            "reviewgate_head_sha": "sha1",
            "reviewgate_config_hash": "ch",
            "reviewgate_pr_metadata_hash": "mh",
        },
    )

    with factory() as verify:
        count = verify.execute(
            select(Analysis).where(Analysis.repository_id == repo_uuid),
        ).scalars().all()
        assert count == []


def test_run_pr_analysis_stub_skips_rate_limit_check_when_redis_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #49: without Redis URL, analysis runs and the limiter is not invoked."""

    import uuid
    from datetime import UTC, datetime

    import dramatiq
    from dramatiq.brokers.stub import StubBroker
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from reviewgate.app.storage.models import Analysis, Installation, Repository

    dramatiq.set_broker(StubBroker())
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)

    def _subset_metadata() -> object:
        from sqlalchemy import MetaData

        md = MetaData()
        Installation.__table__.to_metadata(md)
        Repository.__table__.to_metadata(md)
        Analysis.__table__.to_metadata(md)
        return md

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _subset_metadata().create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    inst_id = uuid.uuid4()
    repo_uuid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    with factory() as setup_session:
        setup_session.add(
            Installation(
                id=inst_id,
                github_installation_id=9400,
                account_login="acme",
                account_type="Organization",
                created_at=now,
            ),
        )
        setup_session.add(
            Repository(
                id=repo_uuid,
                installation_id=inst_id,
                github_repository_id=9494,
                owner="acme",
                name="demo",
                full_name="acme/demo",
                private=False,
                active=True,
                created_at=now,
            ),
        )
        setup_session.commit()

    import reviewgate.app.analysis.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "create_engine_from_settings", lambda _s: engine)
    monkeypatch.setattr(jobs_mod, "create_session_factory", lambda _e: factory)
    monkeypatch.setattr(jobs_mod, "get_cached_final_report", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_mod, "set_cached_final_report", lambda *_a, **_k: None)
    limiter = MagicMock(
        side_effect=AssertionError("check_analysis_rate_limits must not run without Redis"),
    )
    monkeypatch.setattr(jobs_mod, "check_analysis_rate_limits", limiter)

    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub(
        {
            "github_installation_id": 9400,
            "github_repository_id": 9494,
            "reviewgate_repository_id": str(repo_uuid),
            "reviewgate_pull_number": 1,
            "reviewgate_head_sha": "sha1",
            "reviewgate_config_hash": "ch",
            "reviewgate_pr_metadata_hash": "mh",
        },
    )

    limiter.assert_not_called()
    with factory() as verify:
        row = verify.execute(
            select(Analysis).where(Analysis.repository_id == repo_uuid),
        ).scalar_one()
        assert row.status == "completed"


def test_run_pr_analysis_stub_skips_db_when_rate_limit_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #49: non-``ok`` rate limit outcome exits before Postgres lifecycle."""

    import uuid
    from contextlib import nullcontext
    from datetime import UTC, datetime

    import dramatiq
    from dramatiq.brokers.stub import StubBroker
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from reviewgate.app.storage.models import Analysis, Installation, Repository

    dramatiq.set_broker(StubBroker())

    def _subset_metadata() -> object:
        from sqlalchemy import MetaData

        md = MetaData()
        Installation.__table__.to_metadata(md)
        Repository.__table__.to_metadata(md)
        Analysis.__table__.to_metadata(md)
        return md

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _subset_metadata().create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    inst_id = uuid.uuid4()
    repo_uuid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    with factory() as setup_session:
        setup_session.add(
            Installation(
                id=inst_id,
                github_installation_id=9300,
                account_login="acme",
                account_type="Organization",
                created_at=now,
            ),
        )
        setup_session.add(
            Repository(
                id=repo_uuid,
                installation_id=inst_id,
                github_repository_id=9393,
                owner="acme",
                name="demo",
                full_name="acme/demo",
                private=False,
                active=True,
                created_at=now,
            ),
        )
        setup_session.commit()

    import reviewgate.app.analysis.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "create_engine_from_settings", lambda _s: engine)
    monkeypatch.setattr(jobs_mod, "create_session_factory", lambda _e: factory)
    monkeypatch.setattr(jobs_mod, "get_cached_final_report", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs_mod, "set_cached_final_report", lambda *_a, **_k: None)
    monkeypatch.setattr(
        jobs_mod,
        "check_analysis_rate_limits",
        lambda *_a, **_k: "installation_exceeded",
    )
    monkeypatch.setattr(jobs_mod, "worker_job_lock_hold", lambda *_a, **_k: nullcontext(True))
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")

    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub(
        {
            "github_installation_id": 9300,
            "github_repository_id": 9393,
            "reviewgate_repository_id": str(repo_uuid),
            "reviewgate_pull_number": 1,
            "reviewgate_head_sha": "sha1",
            "reviewgate_config_hash": "ch",
            "reviewgate_pr_metadata_hash": "mh",
        },
    )

    with factory() as verify:
        count = verify.execute(
            select(Analysis).where(Analysis.repository_id == repo_uuid),
        ).scalars().all()
        assert count == []


def test_run_pr_analysis_stub_skips_when_enqueue_policy_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker guard no-ops when the installation is no longer eligible (#36)."""

    import dramatiq
    from dramatiq.brokers.stub import StubBroker

    dramatiq.set_broker(StubBroker())

    @contextmanager
    def _session_ctx() -> object:
        yield MagicMock()

    def _fake_session_factory(_engine: object) -> object:
        return lambda: _session_ctx()

    import reviewgate.app.analysis.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "create_engine_from_settings", lambda _s: object())
    monkeypatch.setattr(jobs_mod, "create_session_factory", _fake_session_factory)
    monkeypatch.setattr(
        jobs_mod,
        "installation_repository_may_enqueue_jobs",
        lambda *_a, **_k: False,
    )
    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub(
        {
            "github_installation_id": 1,
            "github_repository_id": 2,
        },
    )


def test_worker_app_import_requires_redis_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``worker_app`` refuses to import without ``REVIEWGATE_REDIS_URL``."""

    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    code = (
        "import importlib, sys\n"
        "try:\n"
        "    importlib.import_module('reviewgate.app.analysis.worker_app')\n"
        "except RuntimeError as exc:\n"
        "    sys.exit(0 if 'REVIEWGATE_REDIS_URL' in str(exc) else 2)\n"
        "sys.exit(1)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
