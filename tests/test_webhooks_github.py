"""Tests for ``POST /webhooks/github`` (issue #33)."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import patch

import pytest
from dramatiq.brokers.stub import StubBroker
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

from reviewgate.app.main import create_app


def _signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_webhook_rejects_bad_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid ``X-Hub-Signature-256`` yields 401 without enqueueing."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "correct_secret")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = b'{"hook": true}'
    with patch(
        "reviewgate.app.analysis.broker_install.RedisBroker",
        lambda **_: StubBroker(),
    ):
        with patch(
            "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
        ) as send:
            with TestClient(create_app()) as client:
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "x-hub-signature-256": _signature(body, "wrong_secret"),
                        "x-github-delivery": "d1",
                        "x-github-event": "ping",
                    },
                )
    assert response.status_code == 401
    send.assert_not_called()


def test_github_webhook_rejects_when_secret_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing webhook secret yields 503."""

    monkeypatch.delenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    with patch(
        "reviewgate.app.analysis.broker_install.RedisBroker",
        lambda **_: StubBroker(),
    ):
        with TestClient(create_app()) as client:
            response = client.post("/webhooks/github", content=b"{}")
    assert response.status_code == 503


def test_github_webhook_rejects_when_redis_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Redis URL yields 503 after signature verification."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = b"{}"
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "s"),
                    "x-github-delivery": "d",
                    "x-github-event": "ping",
                },
            )
    assert response.status_code == 503
    send.assert_not_called()


def test_github_webhook_accepts_valid_signature_and_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid signature returns 202 and calls ``run_pr_analysis_stub.send``."""

    secret = "webhook_test_secret"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = b'{"zen":"pong"}'
    with patch(
        "reviewgate.app.analysis.broker_install.RedisBroker",
        lambda **_: StubBroker(),
    ):
        with patch(
            "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
        ) as send:
            with TestClient(create_app()) as client:
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "x-hub-signature-256": _signature(body, secret),
                        "x-github-delivery": "abc-123",
                        "x-github-event": "ping",
                    },
                )
    assert response.status_code == 202
    send.assert_called_once()
    args, kwargs = send.call_args
    assert args[0] == {
        "github_delivery_id": "abc-123",
        "github_event": "ping",
    }
