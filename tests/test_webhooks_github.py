"""Tests for ``POST /webhooks/github`` (issue #33)."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

import dramatiq
from dramatiq.brokers.stub import StubBroker

from reviewgate.app.analysis import broker_install
from reviewgate.app.main import create_app

_PR_OPENED_BODY = b'{"action":"opened","number":1}'


@pytest.fixture(autouse=True)
def _reset_broker_install_state() -> None:
    """Isolate process-global Dramatiq broker install flags between tests."""

    broker_install._last_installed_redis_url = None
    dramatiq.set_broker(StubBroker())
    yield
    broker_install._last_installed_redis_url = None
    dramatiq.set_broker(StubBroker())


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


def test_github_webhook_reinstalls_broker_when_redis_url_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing ``REVIEWGATE_REDIS_URL`` must not reuse a stale Dramatiq broker."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://host-a:6379/0")
    body = _PR_OPENED_BODY
    broker_urls: list[str | None] = []

    def capture_redis_broker(**kwargs: object) -> StubBroker:
        url_kw = kwargs.get("url")
        broker_urls.append(url_kw if isinstance(url_kw, str) else None)
        return StubBroker()

    with patch(
        "reviewgate.app.analysis.broker_install.RedisBroker",
        side_effect=capture_redis_broker,
    ):
        with patch(
            "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
        ) as send:
            with TestClient(create_app()) as client:
                r1 = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "x-hub-signature-256": _signature(body, "s"),
                        "x-github-delivery": "d1",
                        "x-github-event": "pull_request",
                    },
                )
                assert r1.status_code == 202
                monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://host-b:6379/0")
                r2 = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "x-hub-signature-256": _signature(body, "s"),
                        "x-github-delivery": "d2",
                        "x-github-event": "pull_request",
                    },
                )
                assert r2.status_code == 202

    assert broker_urls == ["redis://host-a:6379/0", "redis://host-b:6379/0"]
    assert send.call_count == 2
