"""Tests for minimal hosted HTML pages (issue #38)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from reviewgate.app.main import create_app


def test_landing_includes_positioning_and_privacy_link() -> None:
    """``GET /`` serves §3 copy and links to ``/privacy``."""

    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    text = response.text
    assert (
        "ReviewGate is a PR intake gate for engineering teams. It flags oversized, "
        "unclear, risky, or mixed-scope pull requests before they reach human reviewers."
    ) in text
    assert 'href="/privacy"' in text
    assert 'href="/feedback"' in text


def test_landing_shows_install_link_when_url_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``REVIEWGATE_GITHUB_APP_INSTALL_URL`` becomes the install CTA href."""

    monkeypatch.setenv(
        "REVIEWGATE_GITHUB_APP_INSTALL_URL",
        "https://github.com/apps/reviewgate-test/installations/new",
    )
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "https://github.com/apps/reviewgate-test/installations/new" in response.text


def test_installation_success_links_onboarding_doc() -> None:
    """``GET /installation-success`` references the beta onboarding guide."""

    client = TestClient(create_app())
    response = client.get("/installation-success")
    assert response.status_code == 200
    assert (
        "https://github.com/leo-aa88/reviewgate/blob/main/docs/ONBOARDING.md"
        in response.text
    )
    assert 'href="/feedback"' in response.text


def test_feedback_page_posts_to_beta_feedback_api() -> None:
    """``GET /feedback`` serves a form wired to ``POST /api/beta-feedback``."""

    client = TestClient(create_app())
    response = client.get("/feedback")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    text = response.text
    assert "/api/beta-feedback" in text
    assert 'name="message"' in text
