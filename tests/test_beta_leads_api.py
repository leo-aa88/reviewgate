"""Tests for ``POST /api/beta-leads`` (issue #39)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

import reviewgate.app.beta_leads as beta_leads_module

from reviewgate.app.main import create_app


def test_beta_leads_returns_503_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REVIEWGATE_DATABASE_URL", raising=False)
    client = TestClient(create_app())
    response = client.post(
        "/api/beta-leads",
        json={"email": "a@example.com"},
    )
    assert response.status_code == 503


def test_beta_leads_rejects_unknown_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql+psycopg://x:y@127.0.0.1:2/db",
    )
    client = TestClient(create_app())
    response = client.post(
        "/api/beta-leads",
        json={"email": "a@example.com", "extra": 1},
    )
    assert response.status_code == 422


def test_beta_leads_requires_email() -> None:
    client = TestClient(create_app())
    response = client.post("/api/beta-leads", json={})
    assert response.status_code == 422


def test_beta_leads_ok_when_persist_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql+psycopg://x:y@127.0.0.1:2/db",
    )
    with patch.object(
        beta_leads_module,
        "persist_beta_lead",
        autospec=True,
    ) as persist:
        client = TestClient(create_app())
        response = client.post(
            "/api/beta-leads",
            json={
                "email": "user@example.com",
                "name": "Ada",
                "company": "Acme",
                "role": "Staff",
                "github_org": "acme",
                "team_size": "10-50",
                "source": "landing",
            },
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    persist.assert_called_once()
    payload = persist.call_args[0][1]
    assert payload.email == "user@example.com"
    assert payload.name == "Ada"


def test_persist_beta_lead_inserts_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit-level insert uses one ``session.add`` + ``commit``."""

    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql+psycopg://x:y@127.0.0.1:2/db",
    )
    from reviewgate.app.beta_leads import BetaLeadRequest, persist_beta_lead
    from reviewgate.app.settings import AppSettings

    fake_engine = object()
    session = MagicMock()
    sm = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = session
    cm.__exit__.return_value = None
    sm.return_value = cm

    with patch(
        "reviewgate.app.beta_leads.create_engine_from_settings",
        return_value=fake_engine,
    ):
        with patch(
            "reviewgate.app.beta_leads.create_session_factory",
            return_value=sm,
        ):
            persist_beta_lead(
                AppSettings(),
                BetaLeadRequest(
                    email="x@y.co",
                    name="  ",
                    company=None,
                    role=None,
                    github_org=None,
                    team_size=None,
                    source="landing",
                ),
            )

    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert added.email == "x@y.co"
    assert added.name is None
    session.commit.assert_called_once()
