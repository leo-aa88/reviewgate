"""Tests for ``GET /privacy`` (issue #37)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from reviewgate.app.main import create_app


def test_privacy_returns_html_with_design_copy() -> None:
    """Public ``/privacy`` includes §21.4 and §23.1 verbatim sentences."""

    client = TestClient(create_app())
    response = client.get("/privacy")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    body = response.text
    assert (
        "ReviewGate evaluates pull request metadata, changed file paths, and compact "
        "diff summaries. It does not clone repositories, execute code, or persist "
        "full repository contents by default."
    ) in body
    assert (
        "If you uninstall ReviewGate, we delete analysis data associated with your "
        "installation within 30 days unless you request deletion sooner."
    ) in body
