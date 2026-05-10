"""Tests for ``webhook_deliveries`` retention purge (issue #34)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("dramatiq")
pytest.importorskip("sqlalchemy")

from reviewgate.app.analysis.jobs import purge_old_webhook_deliveries
from reviewgate.app.storage.webhook_purge import purge_webhook_deliveries_older_than


def test_purge_webhook_deliveries_returns_rowcount() -> None:
    """``purge_webhook_deliveries_older_than`` returns the ORM execute rowcount."""

    session = MagicMock()
    session.execute.return_value.rowcount = 4
    assert purge_webhook_deliveries_older_than(session) == 4


def test_purge_old_webhook_deliveries_fn_noops_without_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The purge actor exits quietly when ``REVIEWGATE_DATABASE_URL`` is unset."""

    monkeypatch.delenv("REVIEWGATE_DATABASE_URL", raising=False)
    purge_old_webhook_deliveries.fn({})
