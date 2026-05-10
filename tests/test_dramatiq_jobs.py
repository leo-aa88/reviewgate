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
