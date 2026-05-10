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

_PR_OPENED_BODY = b'{"action":"opened","number":1}'


def _signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_webhook_rejects_bad_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid ``X-Hub-Signature-256`` yields 401 without enqueueing."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "correct_secret")
    body = b'{"hook": true}'
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
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


def test_github_webhook_rejects_signature_missing_sha256_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``X-Hub-Signature-256`` without the ``sha256=`` prefix yields 401."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    body = b"{}"
    bad_header = hmac.new(b"s", body, hashlib.sha256).hexdigest()
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": bad_header,
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
    with TestClient(create_app()) as client:
        response = client.post("/webhooks/github", content=b"{}")
    assert response.status_code == 503


def test_github_webhook_rejects_when_redis_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Redis yields 503 for ``pull_request`` actions that enqueue."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = _PR_OPENED_BODY
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "s"),
                    "x-github-delivery": "d",
                    "x-github-event": "pull_request",
                },
            )
    assert response.status_code == 503
    send.assert_not_called()


def test_github_webhook_ping_ok_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ping`` returns 202 and never touches Redis or the job queue."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "whsec")
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = b'{"zen":"pong"}'
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "whsec"),
                    "x-github-delivery": "ping-1",
                    "x-github-event": "ping",
                },
            )
    assert response.status_code == 202
    send.assert_not_called()


def test_github_webhook_installation_ack_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installation lifecycle events return 202 without enqueueing."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "whsec")
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = b'{"action":"created"}'
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "whsec"),
                    "x-github-delivery": "inst-1",
                    "x-github-event": "installation",
                },
            )
    assert response.status_code == 202
    send.assert_not_called()


def test_github_webhook_pull_request_edited_without_reviewable_changes_returns_204(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pull_request`` ``edited`` with no title/body/base change yields 204 (§13.2)."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = b'{"action":"edited","changes":{}}'
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "s"),
                    "x-github-delivery": "d",
                    "x-github-event": "pull_request",
                },
            )
    assert response.status_code == 204
    send.assert_not_called()


def test_github_webhook_pull_request_edited_title_change_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pull_request`` ``edited`` with a title change enqueues."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = b'{"action":"edited","changes":{"title":{"from":"old"}}}'
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
                        "x-hub-signature-256": _signature(body, "s"),
                        "x-github-delivery": "d",
                        "x-github-event": "pull_request",
                    },
                )
    assert response.status_code == 202
    send.assert_called_once()
    assert send.call_args[0][0]["github_pull_request_action"] == "edited"


def test_github_webhook_pull_request_labeled_returns_204(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported ``pull_request`` actions are acknowledged with 204."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = b'{"action":"labeled"}'
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "s"),
                    "x-github-delivery": "d",
                    "x-github-event": "pull_request",
                },
            )
    assert response.status_code == 204
    send.assert_not_called()


def test_github_webhook_unknown_event_returns_204(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-PR events outside the ack set yield 204."""

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
                    "x-github-event": "issues",
                },
            )
    assert response.status_code == 204
    send.assert_not_called()


def test_github_webhook_pull_request_invalid_json_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON on a ``pull_request`` event yields 400."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    body = b"{not-json"
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "s"),
                    "x-github-delivery": "d",
                    "x-github-event": "pull_request",
                },
            )
    assert response.status_code == 400
    send.assert_not_called()


def test_github_webhook_accepts_valid_signature_and_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pull_request`` ``opened`` returns 202 and calls ``run_pr_analysis_stub.send``."""

    secret = "webhook_test_secret"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY
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
                        "x-github-event": "pull_request",
                    },
                )
    assert response.status_code == 202
    send.assert_called_once()
    args, kwargs = send.call_args
    assert args[0] == {
        "github_delivery_id": "abc-123",
        "github_event": "pull_request",
        "github_pull_request_action": "opened",
    }
