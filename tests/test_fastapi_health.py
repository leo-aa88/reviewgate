"""Tests for the FastAPI skeleton (issue #32)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from reviewgate.app.main import create_app


def test_health_returns_ok_json() -> None:
    """``GET /health`` matches ``docs/DESIGN.md`` §17.2."""

    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
