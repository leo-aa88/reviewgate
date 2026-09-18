"""Unit tests for delivery claim lease, release, and mark_processed (issue #154)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from reviewgate.app.settings import AppSettings
from reviewgate.app.webhooks.dedupe import (
    claim_github_webhook_delivery,
    mark_github_webhook_delivery_processed,
    release_github_webhook_delivery,
)


@pytest.fixture
def app_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql+psycopg://x:y@127.0.0.1:2/db",
    )
    return AppSettings()


def _session_context(session: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__enter__.return_value = session
    cm.__exit__.return_value = None
    sm = MagicMock(return_value=cm)
    return sm


def test_claim_github_webhook_delivery_requires_database_url() -> None:
    settings = AppSettings(database_url=None)
    with pytest.raises(RuntimeError, match="requires REVIEWGATE_DATABASE_URL"):
        claim_github_webhook_delivery(
            settings,
            delivery_id="d1",
            event_name="pull_request",
        )


def test_mark_github_webhook_delivery_processed_requires_database_url() -> None:
    settings = AppSettings(database_url=None)
    with pytest.raises(RuntimeError, match="requires REVIEWGATE_DATABASE_URL"):
        mark_github_webhook_delivery_processed(
            settings,
            delivery_id="d1",
        )


def test_claim_github_webhook_delivery_new_row_claimed(
    app_settings: AppSettings,
) -> None:
    """1. New delivery is atomically inserted and returns ('claimed', token)."""
    from sqlalchemy.dialects import postgresql

    fake_engine = object()
    session = MagicMock()
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = uuid.uuid4()
    session.execute.return_value = first_result
    sm = _session_context(session)

    with patch(
        "reviewgate.app.webhooks.dedupe.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.dedupe.create_session_factory",
            return_value=sm,
        ):
            status, token = claim_github_webhook_delivery(
                app_settings,
                delivery_id="deliv-new-1",
                event_name="pull_request",
            )

    assert status == "claimed"
    assert isinstance(token, uuid.UUID)
    session.execute.assert_called_once()
    executed_stmt = session.execute.call_args[0][0]
    compiled_sql = str(
        executed_stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "INSERT INTO webhook_deliveries" in compiled_sql
    assert "ON CONFLICT (github_delivery_id) DO UPDATE" in compiled_sql
    assert "webhook_deliveries.processed IS false" in compiled_sql
    assert "webhook_deliveries.claimed_at <" in compiled_sql
    assert "RETURNING webhook_deliveries.id" in compiled_sql
    assert str(token) in compiled_sql
    session.commit.assert_called_once()


def test_claim_github_webhook_delivery_processed_is_duplicate(
    app_settings: AppSettings,
) -> None:
    """2. Processed delivery (processed=True) fails the update WHERE clause, select returns True -> 'duplicate'."""
    fake_engine = object()
    session = MagicMock()
    # First execute is upsert (returns None), second is select processed (returns True)
    upsert_res = MagicMock()
    upsert_res.scalar_one_or_none.return_value = None
    select_res = MagicMock()
    select_res.scalar_one_or_none.return_value = True
    session.execute.side_effect = [upsert_res, select_res]
    sm = _session_context(session)

    with patch(
        "reviewgate.app.webhooks.dedupe.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.dedupe.create_session_factory",
            return_value=sm,
        ):
            status, token = claim_github_webhook_delivery(
                app_settings,
                delivery_id="deliv-processed-dup",
                event_name="pull_request",
            )

    assert status == "duplicate"
    assert token is None
    assert session.execute.call_count == 2
    assert session.commit.call_count == 1


def test_claim_github_webhook_delivery_concurrent_in_progress_is_active(
    app_settings: AppSettings,
) -> None:
    """3. Active in-progress delivery (processed=False, valid lease) returns ('active', None)."""
    fake_engine = object()
    session = MagicMock()
    # First execute is upsert (returns None because lease is not stale), second is select processed (returns False)
    upsert_res = MagicMock()
    upsert_res.scalar_one_or_none.return_value = None
    select_res = MagicMock()
    select_res.scalar_one_or_none.return_value = False
    session.execute.side_effect = [upsert_res, select_res]
    sm = _session_context(session)

    with patch(
        "reviewgate.app.webhooks.dedupe.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.dedupe.create_session_factory",
            return_value=sm,
        ):
            status, token = claim_github_webhook_delivery(
                app_settings,
                delivery_id="deliv-in-flight",
                event_name="pull_request",
            )

    assert status == "active"
    assert token is None
    assert session.execute.call_count == 2
    assert session.commit.call_count == 1


def test_claim_github_webhook_delivery_expired_lease_reclaims(
    app_settings: AppSettings,
) -> None:
    """4. An expired processing lease (e.g. after worker crash or release) is reclaimed with a new token."""
    from sqlalchemy.dialects import postgresql

    fake_engine = object()
    session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = uuid.uuid4()
    session.execute.return_value = exec_result
    sm = _session_context(session)

    with patch(
        "reviewgate.app.webhooks.dedupe.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.dedupe.create_session_factory",
            return_value=sm,
        ):
            status, token = claim_github_webhook_delivery(
                app_settings,
                delivery_id="deliv-crashed-reclaimed",
                event_name="pull_request",
                lease_timeout_seconds=30,
            )

    assert status == "claimed"
    assert isinstance(token, uuid.UUID)
    session.execute.assert_called_once()
    executed_stmt = session.execute.call_args[0][0]
    compiled_sql = str(
        executed_stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "ON CONFLICT (github_delivery_id) DO UPDATE" in compiled_sql
    assert "webhook_deliveries.processed IS false" in compiled_sql
    assert "webhook_deliveries.claimed_at <" in compiled_sql
    assert str(token) in compiled_sql
    session.commit.assert_called_once()


def test_release_github_webhook_delivery_success_with_token(
    app_settings: AppSettings,
) -> None:
    """5. release_github_webhook_delivery with matching claim_token resets claimed_at to epoch."""
    fake_engine = object()
    session = MagicMock()
    sm = _session_context(session)
    token = uuid.uuid4()

    with patch(
        "reviewgate.app.webhooks.dedupe.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.dedupe.create_session_factory",
            return_value=sm,
        ):
            release_github_webhook_delivery(
                app_settings,
                delivery_id="deliv-fail-release",
                claim_token=token,
            )

    session.execute.assert_called_once()
    executed_stmt = session.execute.call_args[0][0]
    compiled_str = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "webhook_deliveries.claim_token =" in compiled_str
    session.commit.assert_called_once()


def test_release_github_webhook_delivery_stale_owner_cannot_release_newer_owner(
    app_settings: AppSettings,
) -> None:
    """6. A stale owner with old claim_token includes its token in the WHERE clause, ensuring it does not release newer owner's lease."""
    fake_engine = object()
    session = MagicMock()
    sm = _session_context(session)
    old_token = uuid.uuid4()

    with patch(
        "reviewgate.app.webhooks.dedupe.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.dedupe.create_session_factory",
            return_value=sm,
        ):
            release_github_webhook_delivery(
                app_settings,
                delivery_id="deliv-stale-race",
                claim_token=old_token,
            )

    session.execute.assert_called_once()
    executed_stmt = session.execute.call_args[0][0]
    compiled_str = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert (old_token.hex in compiled_str or str(old_token) in compiled_str)
    session.commit.assert_called_once()


def test_release_github_webhook_delivery_noop_when_database_url_unset() -> None:

    """release_github_webhook_delivery handles unset database_url gracefully."""
    settings = AppSettings(database_url=None)
    release_github_webhook_delivery(settings, delivery_id="d1")


def test_claim_github_webhook_delivery_database_error_returns_unavailable(
    app_settings: AppSettings,
) -> None:
    """OperationalError during claim rolls back and returns ('database_unavailable', None)."""
    fake_engine = object()
    session = MagicMock()
    session.execute.side_effect = OperationalError("conn failed", {}, Exception())
    sm = _session_context(session)

    with patch(
        "reviewgate.app.webhooks.dedupe.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.dedupe.create_session_factory",
            return_value=sm,
        ):
            status, token = claim_github_webhook_delivery(
                app_settings,
                delivery_id="deliv-err-1",
                event_name="pull_request",
            )

    assert status == "database_unavailable"
    assert token is None
    session.rollback.assert_called_once()


def test_mark_github_webhook_delivery_processed_success_with_token(
    app_settings: AppSettings,
) -> None:
    """mark_github_webhook_delivery_processed executes update setting processed=True and commits."""
    fake_engine = object()
    session = MagicMock()
    sm = _session_context(session)
    token = uuid.uuid4()

    with patch(
        "reviewgate.app.webhooks.dedupe.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.dedupe.create_session_factory",
            return_value=sm,
        ):
            mark_github_webhook_delivery_processed(
                app_settings,
                delivery_id="deliv-done-1",
                claim_token=token,
            )

    session.execute.assert_called_once()
    executed_stmt = session.execute.call_args[0][0]
    compiled_str = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "UPDATE webhook_deliveries SET processed=true" in compiled_str
    assert "webhook_deliveries.github_delivery_id = 'deliv-done-1'" in compiled_str
    assert (token.hex in compiled_str or str(token) in compiled_str)
    session.commit.assert_called_once()


def test_mark_github_webhook_delivery_processed_operational_error_raises(
    app_settings: AppSettings,
) -> None:
    """OperationalError during mark_processed rolls back and re-raises."""
    fake_engine = object()
    session = MagicMock()
    session.execute.side_effect = OperationalError("conn failed", {}, Exception())
    sm = _session_context(session)

    with patch(
        "reviewgate.app.webhooks.dedupe.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.dedupe.create_session_factory",
            return_value=sm,
        ):
            with pytest.raises(OperationalError):
                mark_github_webhook_delivery_processed(
                    app_settings,
                    delivery_id="deliv-err-1",
                )

    session.rollback.assert_called_once()
