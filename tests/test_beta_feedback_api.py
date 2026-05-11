"""Tests for ``POST /api/beta-feedback`` (issue #55)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

import reviewgate.app.beta_feedback as beta_feedback_module

from reviewgate.app.main import create_app


def test_beta_feedback_returns_503_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REVIEWGATE_DATABASE_URL", raising=False)
    client = TestClient(create_app())
    response = client.post(
        "/api/beta-feedback",
        json={"message": "Great product"},
    )
    assert response.status_code == 503


def test_beta_feedback_rejects_unknown_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql+psycopg://x:y@127.0.0.1:2/db",
    )
    client = TestClient(create_app())
    response = client.post(
        "/api/beta-feedback",
        json={"message": "Hi", "extra": 1},
    )
    assert response.status_code == 422


def test_beta_feedback_requires_message() -> None:
    client = TestClient(create_app())
    response = client.post("/api/beta-feedback", json={})
    assert response.status_code == 422


def test_beta_feedback_rejects_blank_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql+psycopg://x:y@127.0.0.1:2/db",
    )
    client = TestClient(create_app())
    response = client.post("/api/beta-feedback", json={"message": "   "})
    assert response.status_code == 422


def test_beta_feedback_ok_when_persist_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql+psycopg://x:y@127.0.0.1:2/db",
    )
    with patch.object(
        beta_feedback_module,
        "persist_beta_feedback",
        autospec=True,
    ) as persist:
        client = TestClient(create_app())
        response = client.post(
            "/api/beta-feedback",
            json={"message": "  The checks are helpful.  ", "contact": "  a@b.co  "},
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    persist.assert_called_once()
    payload = persist.call_args[0][1]
    assert payload.message == "The checks are helpful."
    assert payload.contact == "a@b.co"


def test_persist_beta_feedback_inserts_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit-level insert uses one ``session.add`` + ``commit``."""

    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql+psycopg://x:y@127.0.0.1:2/db",
    )
    from reviewgate.app.beta_feedback import BetaFeedbackRequest, persist_beta_feedback
    from reviewgate.app.settings import AppSettings

    fake_engine = object()
    session = MagicMock()
    sm = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = session
    cm.__exit__.return_value = None
    sm.return_value = cm

    with patch(
        "reviewgate.app.beta_feedback.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.beta_feedback.create_session_factory",
            return_value=sm,
        ):
            persist_beta_feedback(
                AppSettings(),
                BetaFeedbackRequest(message="Note", contact="  "),
            )

    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert added.message == "Note"
    assert added.contact is None
    session.commit.assert_called_once()
