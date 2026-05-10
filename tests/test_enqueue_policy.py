"""Unit tests for ``reviewgate.app.webhooks.enqueue_policy`` (issue #36)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from reviewgate.app.webhooks.enqueue_policy import installation_repository_may_enqueue_jobs


def test_may_enqueue_true_when_repository_unknown() -> None:
    session = MagicMock()
    result = MagicMock()
    result.one_or_none.return_value = None
    session.execute.return_value = result
    assert installation_repository_may_enqueue_jobs(
        session,
        github_installation_id=1,
        github_repository_id=2,
    )


def test_may_enqueue_false_when_installation_soft_deleted() -> None:
    session = MagicMock()
    repo = MagicMock(active=True)
    inst = MagicMock(
        github_installation_id=9,
        deleted_at=datetime.now(timezone.utc),
    )
    result = MagicMock()
    result.one_or_none.return_value = (repo, inst)
    session.execute.return_value = result
    assert not installation_repository_may_enqueue_jobs(
        session,
        github_installation_id=9,
        github_repository_id=3,
    )


def test_may_enqueue_false_when_repository_inactive() -> None:
    session = MagicMock()
    repo = MagicMock(active=False)
    inst = MagicMock(github_installation_id=9, deleted_at=None)
    result = MagicMock()
    result.one_or_none.return_value = (repo, inst)
    session.execute.return_value = result
    assert not installation_repository_may_enqueue_jobs(
        session,
        github_installation_id=9,
        github_repository_id=3,
    )


def test_may_enqueue_true_when_installation_id_mismatch() -> None:
    """Stale rows after GitHub reassignment must not block processing."""

    session = MagicMock()
    repo = MagicMock(active=False)
    inst = MagicMock(github_installation_id=8, deleted_at=None)
    result = MagicMock()
    result.one_or_none.return_value = (repo, inst)
    session.execute.return_value = result
    assert installation_repository_may_enqueue_jobs(
        session,
        github_installation_id=9,
        github_repository_id=3,
    )
