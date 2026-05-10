"""Unit tests for ``persist_installation_webhook_payload`` (issue #35)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from reviewgate.app.settings import AppSettings
from reviewgate.app.webhooks.installation_persist import persist_installation_webhook_payload


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


def test_persist_installation_created_requires_installation_object(
    app_settings: AppSettings,
) -> None:
    with pytest.raises(ValueError, match="missing installation"):
        persist_installation_webhook_payload(
            app_settings,
            event_name="installation",
            action="created",
            payload={"action": "created"},
        )


def test_persist_installation_repositories_removed_requires_installation_row(
    app_settings: AppSettings,
) -> None:
    """``removed`` fails fast when the installation row is unknown."""

    fake_engine = object()
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    sm = _session_context(session)

    with patch(
        "reviewgate.app.webhooks.installation_persist.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.installation_persist.create_session_factory",
            return_value=sm,
        ):
            with pytest.raises(ValueError, match="not registered"):
                persist_installation_webhook_payload(
                    app_settings,
                    event_name="installation_repositories",
                    action="removed",
                    payload={
                        "installation": {
                            "id": 1,
                            "account": {"login": "a", "type": "User"},
                        },
                        "repositories_removed": [{"id": 9}],
                    },
                )


def test_persist_installation_repositories_added_rejects_non_object_entries(
    app_settings: AppSettings,
) -> None:
    fake_engine = object()
    session = MagicMock()
    first = MagicMock()
    first.scalar_one.return_value = uuid.uuid4()
    session.execute.return_value = first
    sm = _session_context(session)

    with patch(
        "reviewgate.app.webhooks.installation_persist.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.installation_persist.create_session_factory",
            return_value=sm,
        ):
            with pytest.raises(ValueError, match="repositories_added entries"):
                persist_installation_webhook_payload(
                    app_settings,
                    event_name="installation_repositories",
                    action="added",
                    payload={
                        "installation": {
                            "id": 1,
                            "account": {"login": "a", "type": "User"},
                        },
                        "repositories_added": ["not-an-object"],
                    },
                )


def test_persist_installation_created_commits_after_upserts(
    app_settings: AppSettings,
) -> None:
    """``installation.created`` issues an installation upsert plus one repo upsert."""

    fake_engine = object()
    session = MagicMock()
    inst_id = uuid.uuid4()
    first = MagicMock()
    first.scalar_one.return_value = inst_id
    second = MagicMock()
    session.execute.side_effect = [first, second]
    sm = _session_context(session)

    with patch(
        "reviewgate.app.webhooks.installation_persist.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.webhooks.installation_persist.create_session_factory",
            return_value=sm,
        ):
            persist_installation_webhook_payload(
                app_settings,
                event_name="installation",
                action="created",
                payload={
                    "action": "created",
                    "installation": {
                        "id": 42,
                        "account": {"login": "org", "type": "Organization"},
                    },
                    "repositories": [
                        {
                            "id": 7,
                            "name": "r",
                            "full_name": "org/r",
                            "private": False,
                            "owner": {"login": "org"},
                        },
                    ],
                },
            )

    assert session.execute.call_count == 2
    session.commit.assert_called_once()
