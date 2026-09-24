"""Tests for ``POST /webhooks/github`` (issue #33)."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Literal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")

import dramatiq
from dramatiq.brokers.stub import StubBroker

import reviewgate.app.webhooks.github as github_webhook_module

from reviewgate.app.analysis import broker_install
from reviewgate.app.main import create_app

_PR_OPENED_BODY = (
    b'{"action":"opened","number":1,'
    b'"installation":{"id":1111},"repository":{"id":2222}}'
)

_PR_SYNCHRONIZE_BODY = (
    b'{"action":"synchronize","number":1,'
    b'"installation":{"id":1111},'
    b'"repository":{"id":2222,"name":"reviewgate","owner":{"login":"leo-aa88"}}}'
)


@pytest.fixture(autouse=True)
def _stub_github_webhook_delivery_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy DATABASE_URL gating without contacting Postgres in unit tests."""

    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql://unused:unused@127.0.0.1:9/unused",
    )

    def _claim(
        _settings: object,
        *,
        delivery_id: str,
        event_name: str,
    ) -> tuple[Literal["claimed"], uuid.UUID]:
        return ("claimed", uuid.uuid4())

    monkeypatch.setattr(
        github_webhook_module,
        "claim_github_webhook_delivery",
        _claim,
    )
    monkeypatch.setattr(
        github_webhook_module,
        "mark_github_webhook_delivery_processed",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        github_webhook_module,
        "release_github_webhook_delivery",
        lambda *_a, **_k: None,
    )


@pytest.fixture(autouse=True)
def _stub_persist_installation_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid live Postgres for installation webhooks (issue #35 unit tests)."""

    monkeypatch.setattr(
        github_webhook_module,
        "persist_installation_webhook_payload",
        lambda *_a, **_k: None,
    )


@pytest.fixture(autouse=True)
def _stub_pull_request_may_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid live Postgres on the ``pull_request`` enqueue guard (issue #36)."""

    monkeypatch.setattr(
        github_webhook_module,
        "pull_request_may_enqueue",
        lambda *_a, **_k: True,
    )


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


def test_github_webhook_rejects_when_database_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pull_request`` enqueue requires ``REVIEWGATE_DATABASE_URL``."""

    monkeypatch.delenv("REVIEWGATE_DATABASE_URL", raising=False)
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
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


def test_github_webhook_installation_created_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``installation`` ``created`` returns 202 without Redis or PR enqueue."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "whsec")
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = (
        b'{"action":"created","installation":{'
        b'"id":12345,"account":{"login":"acme","type":"Organization"}},'
        b'"repositories":[]}'
    )
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


def test_github_webhook_installation_deleted_legacy_204_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional rollback flag restores the pre-#36 **204** no-op for ``deleted``."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "whsec")
    monkeypatch.setenv("REVIEWGATE_LEGACY_INSTALLATION_DELETED_WEBHOOK_204", "true")
    body = b'{"action":"deleted","installation":{"id":1}}'
    with patch.object(
        github_webhook_module,
        "persist_installation_webhook_payload",
    ) as persist:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "whsec"),
                    "x-github-delivery": "del-legacy",
                    "x-github-event": "installation",
                },
            )
    assert response.status_code == 204
    persist.assert_not_called()


def test_github_webhook_installation_deleted_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``installation`` ``deleted`` is persisted like other mutation events (#36)."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "whsec")
    monkeypatch.delenv("REVIEWGATE_LEGACY_INSTALLATION_DELETED_WEBHOOK_204", raising=False)
    body = b'{"action":"deleted","installation":{"id":1}}'
    with patch.object(
        github_webhook_module,
        "persist_installation_webhook_payload",
    ) as persist:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "whsec"),
                    "x-github-delivery": "del-1",
                    "x-github-event": "installation",
                },
            )
    assert response.status_code == 202
    persist.assert_called_once()
    assert persist.call_args.kwargs["action"] == "deleted"


def test_github_webhook_installation_created_invokes_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``installation`` ``created`` calls ``persist_installation_webhook_payload``."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = (
        b'{"action":"created","installation":{'
        b'"id":99,"account":{"login":"org","type":"Organization"}},'
        b'"repositories":[{"id":1,"name":"r","full_name":"org/r",'
        b'"private":false,"owner":{"login":"org"}}]}'
    )
    with patch.object(
        github_webhook_module,
        "persist_installation_webhook_payload",
    ) as persist:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "s"),
                    "x-github-delivery": "ic-1",
                    "x-github-event": "installation",
                },
            )
    assert response.status_code == 202
    persist.assert_called_once()
    kwargs = persist.call_args.kwargs
    assert kwargs["event_name"] == "installation"
    assert kwargs["action"] == "created"


def test_github_webhook_installation_repositories_removed_calls_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``installation_repositories`` ``removed`` triggers persistence."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = (
        b'{"action":"removed","installation":{'
        b'"id":7,"account":{"login":"u","type":"User"}},'
        b'"repositories_removed":[{"id":100}]}'
    )
    with patch.object(
        github_webhook_module,
        "persist_installation_webhook_payload",
    ) as persist:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "s"),
                    "x-github-delivery": "ir-1",
                    "x-github-event": "installation_repositories",
                },
            )
    assert response.status_code == 202
    persist.assert_called_once()
    assert persist.call_args.kwargs["action"] == "removed"


def test_github_webhook_installation_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``installation`` ``created`` requires ``REVIEWGATE_DATABASE_URL``."""

    monkeypatch.delenv("REVIEWGATE_DATABASE_URL", raising=False)
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    body = (
        b'{"action":"created","installation":{'
        b'"id":1,"account":{"login":"x","type":"User"}},"repositories":[]}'
    )
    with patch.object(
        github_webhook_module,
        "persist_installation_webhook_payload",
    ) as persist:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "s"),
                    "x-github-delivery": "d",
                    "x-github-event": "installation",
                },
            )
    assert response.status_code == 503
    persist.assert_not_called()


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


def test_github_webhook_pull_request_edited_base_change_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pull_request`` ``edited`` with a ``changes.base`` update enqueues (§13.2)."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = b'{"action":"edited","changes":{"base":{"ref":{"from":"main","to":"dev"}}}}'
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
                        "x-github-delivery": "d-base",
                        "x-github-event": "pull_request",
                    },
                )
    assert response.status_code == 202
    send.assert_called_once()


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


def test_github_webhook_database_unavailable_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``database_unavailable`` from the claim path yields a retryable 503."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("database_unavailable", None),
    ):
        with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
            with TestClient(create_app()) as client:
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "x-hub-signature-256": _signature(body, "s"),
                        "x-github-delivery": "d-db",
                        "x-github-event": "pull_request",
                    },
                )
    assert response.status_code == 503
    send.assert_not_called()


def test_github_webhook_duplicate_delivery_returns_202_without_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate ``github_delivery_id`` returns **202** without ``.send`` (§13.3)."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("duplicate", None),
    ):
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
                            "x-github-delivery": "dup-1",
                            "x-github-event": "pull_request",
                        },
                    )
    assert response.status_code == 202
    send.assert_not_called()


def test_github_webhook_missing_delivery_id_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enqueueable ``pull_request`` events require ``X-GitHub-Delivery``."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY
    with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
        with TestClient(create_app()) as client:
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, "secret"),
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
        "github_installation_id": 1111,
        "github_repository_id": 2222,
    }


def test_github_webhook_pull_request_when_enqueue_blocked_returns_202(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft-deleted installs acknowledge **202** without enqueue (issue #36)."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY
    with patch.object(
        github_webhook_module,
        "pull_request_may_enqueue",
        return_value=False,
    ):
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
                            "x-github-delivery": "blocked-1",
                            "x-github-event": "pull_request",
                        },
                    )
    assert response.status_code == 202
    send.assert_not_called()


def test_github_webhook_synchronize_debounce_skips_enqueue_when_coalesced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #45: coalesced ``synchronize`` returns **202** without queueing."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY
    with patch.object(
        github_webhook_module,
        "synchronize_debounce_allows_enqueue",
        return_value=False,
    ):
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
                            "x-github-delivery": "debounce-1",
                            "x-github-event": "pull_request",
                        },
                    )
    assert response.status_code == 202
    send.assert_not_called()


def test_github_webhook_synchronize_debounce_redis_error_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #45: Redis failures during debounce must not enqueue."""

    import redis.exceptions

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY

    def _boom(*_a: object, **_k: object) -> bool:
        raise redis.exceptions.ConnectionError("simulated")

    with patch.object(
        github_webhook_module,
        "synchronize_debounce_allows_enqueue",
        side_effect=_boom,
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
                        "x-github-delivery": "debounce-redis-err",
                        "x-github-event": "pull_request",
                    },
                )
    assert response.status_code == 503
    send.assert_not_called()


def test_github_webhook_skip_enqueue_when_completed_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #47: §13.7 enqueue dedupe may return **202** without ``Actor.send``."""

    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY
    with patch.object(
        github_webhook_module,
        "evaluate_pull_request_enqueue_dedupe",
        lambda *_a, **_k: (True, {}),
    ):
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
                            "x-github-delivery": "skip-completed-1",
                            "x-github-event": "pull_request",
                        },
                    )
    assert response.status_code == 202
    send.assert_not_called()


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


def test_github_webhook_successful_enqueue_marks_delivery_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: Successful enqueue marks the delivery as processed."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY

    with patch.object(
        github_webhook_module,
        "mark_github_webhook_delivery_processed",
    ) as mock_mark:
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
                            "x-github-delivery": "delivery-enqueue-ok",
                            "x-github-event": "pull_request",
                        },
                    )

    assert response.status_code == 202
    send.assert_called_once()
    mock_mark.assert_called_once()
    assert mock_mark.call_args.kwargs["delivery_id"] == "delivery-enqueue-ok"


def test_github_webhook_enqueue_failure_does_not_mark_delivery_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: If enqueue fails, delivery is NOT marked processed so it can be retried."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY

    with patch.object(
        github_webhook_module,
        "mark_github_webhook_delivery_processed",
    ) as mock_mark:
        with patch(
            "reviewgate.app.analysis.broker_install.RedisBroker",
            lambda **_: StubBroker(),
        ):
            with patch(
                "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
                side_effect=RuntimeError("transient queue failure"),
            ):
                with TestClient(create_app(), raise_server_exceptions=False) as client:
                    response = client.post(
                        "/webhooks/github",
                        content=body,
                        headers={
                            "x-hub-signature-256": _signature(body, secret),
                            "x-github-delivery": "delivery-enqueue-fail",
                            "x-github-event": "pull_request",
                        },
                    )

    assert response.status_code == 500
    mock_mark.assert_not_called()


def test_github_webhook_unprocessed_delivery_retry_enqueues_successfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: An unprocessed delivery can be retried and successfully enqueued."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY

    # Simulate claim_github_webhook_delivery returning ("claimed", token) on retry of unprocessed delivery
    token = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token),
    ) as mock_claim:
        with patch.object(
            github_webhook_module,
            "mark_github_webhook_delivery_processed",
        ) as mock_mark:
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
                                "x-github-delivery": "delivery-retry-ok",
                                "x-github-event": "pull_request",
                            },
                        )

    assert response.status_code == 202
    mock_claim.assert_called_once()
    send.assert_called_once()
    mock_mark.assert_called_once()
    assert mock_mark.call_args.kwargs["delivery_id"] == "delivery-retry-ok"
    assert mock_mark.call_args.kwargs["claim_token"] == token


def test_github_webhook_processed_delivery_is_treated_as_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: A processed delivery is treated as duplicate and not re-enqueued."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY

    # Simulate claim_github_webhook_delivery returning ("duplicate", None) because delivery is already processed
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("duplicate", None),
    ) as mock_claim:
        with patch.object(
            github_webhook_module,
            "mark_github_webhook_delivery_processed",
        ) as mock_mark:
            with patch(
                "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
            ) as send:
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/webhooks/github",
                        content=body,
                        headers={
                            "x-hub-signature-256": _signature(body, secret),
                            "x-github-delivery": "delivery-already-processed",
                            "x-github-event": "pull_request",
                        },
                    )

    assert response.status_code == 202
    mock_claim.assert_called_once()
    send.assert_not_called()
    mock_mark.assert_not_called()


def test_github_webhook_active_claim_returns_retryable_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: An active in-progress delivery claim returns 503 so GitHub retries."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY

    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("active", None),
    ) as mock_claim:
        with patch.object(
            github_webhook_module,
            "mark_github_webhook_delivery_processed",
        ) as mock_mark:
            with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/webhooks/github",
                        content=body,
                        headers={
                            "x-hub-signature-256": _signature(body, secret),
                            "x-github-delivery": "delivery-active-inflight",
                            "x-github-event": "pull_request",
                        },
                    )

    assert response.status_code == 503
    mock_claim.assert_called_once()
    send.assert_not_called()
    mock_mark.assert_not_called()


def test_github_webhook_broker_install_failure_cleans_up_debounce_and_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: Failure in broker install releases debounce and delivery claim."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY

    token = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token),
    ):
        with patch.object(
            github_webhook_module,
            "synchronize_debounce_allows_enqueue",
            return_value=True,
        ):
            with patch.object(
                github_webhook_module,
                "install_redis_broker",
                side_effect=RuntimeError("broker failure"),
            ):
                with patch.object(
                    github_webhook_module,
                    "release_synchronize_debounce",
                ) as mock_release_debounce:
                    with patch.object(
                        github_webhook_module,
                        "release_github_webhook_delivery",
                    ) as mock_release_claim:
                        with TestClient(create_app(), raise_server_exceptions=False) as client:
                            response = client.post(
                                "/webhooks/github",
                                content=body,
                                headers={
                                    "x-hub-signature-256": _signature(body, secret),
                                    "x-github-delivery": "delivery-broker-fail",
                                    "x-github-event": "pull_request",
                                },
                            )

    assert response.status_code == 500
    mock_release_debounce.assert_called_once()
    assert mock_release_debounce.call_args.kwargs["delivery_id"] == "delivery-broker-fail"
    mock_release_claim.assert_called_once()
    assert mock_release_claim.call_args.kwargs["delivery_id"] == "delivery-broker-fail"
    assert mock_release_claim.call_args.kwargs["claim_token"] == token


def test_github_webhook_enqueue_failure_cleans_up_debounce_and_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: Failure in job send releases debounce and delivery claim."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY

    token = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token),
    ):
        with patch.object(
            github_webhook_module,
            "synchronize_debounce_allows_enqueue",
            return_value=True,
        ):
            with patch(
                "reviewgate.app.analysis.broker_install.RedisBroker",
                lambda **_: StubBroker(),
            ):
                with patch(
                    "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
                    side_effect=RuntimeError("queue down"),
                ):
                    with patch.object(
                        github_webhook_module,
                        "release_synchronize_debounce",
                    ) as mock_release_debounce:
                        with patch.object(
                            github_webhook_module,
                            "release_github_webhook_delivery",
                        ) as mock_release_claim:
                            with TestClient(create_app(), raise_server_exceptions=False) as client:
                                response = client.post(
                                    "/webhooks/github",
                                    content=body,
                                    headers={
                                        "x-hub-signature-256": _signature(body, secret),
                                        "x-github-delivery": "delivery-enqueue-fail-2",
                                        "x-github-event": "pull_request",
                                    },
                                )

    assert response.status_code == 500
    mock_release_debounce.assert_called_once()
    assert mock_release_debounce.call_args.kwargs["delivery_id"] == "delivery-enqueue-fail-2"
    mock_release_claim.assert_called_once()
    assert mock_release_claim.call_args.kwargs["delivery_id"] == "delivery-enqueue-fail-2"
    assert mock_release_claim.call_args.kwargs["claim_token"] == token


def test_github_webhook_send_success_mark_processed_failure_does_not_release_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: If send succeeds but mark_processed fails, the claim is NOT released.

    This bounds the duplicate-enqueue window to one lease (a redelivery gets
    "active" until ``webhook_delivery_lease_seconds`` expires); it does not
    make duplicate enqueue impossible. Final duplicate-processing protection
    is the worker's §13.7 ``worker_job_lock_hold`` and ``analyses`` lifecycle.
    """

    from sqlalchemy.exc import OperationalError

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY

    token = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token),
    ):
        with patch.object(
            github_webhook_module,
            "mark_github_webhook_delivery_processed",
            side_effect=OperationalError("db connection lost", {}, Exception()),
        ):
            with patch.object(
                github_webhook_module,
                "release_github_webhook_delivery",
            ) as mock_release:
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
                                    "x-github-delivery": "delivery-mark-fail",
                                    "x-github-event": "pull_request",
                                },
                            )

    assert response.status_code == 503
    send.assert_called_once()
    mock_release.assert_not_called()



def test_github_webhook_mark_processed_database_unavailable_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: Database failure while marking processed yields 503."""

    from sqlalchemy.exc import OperationalError

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY

    with patch.object(
        github_webhook_module,
        "mark_github_webhook_delivery_processed",
        side_effect=OperationalError("db conn lost", {}, Exception()),
    ):
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
                            "x-github-delivery": "delivery-db-err",
                            "x-github-event": "pull_request",
                        },
                    )

    assert response.status_code == 503
    send.assert_called_once()


def test_github_webhook_pull_request_enqueue_blocked_marks_delivery_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: Soft-deleted/blocked enqueue path acknowledges 202 and marks delivery processed."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY

    with patch.object(
        github_webhook_module,
        "pull_request_may_enqueue",
        return_value=False,
    ):
        with patch.object(
            github_webhook_module,
            "mark_github_webhook_delivery_processed",
        ) as mock_mark:
            with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/webhooks/github",
                        content=body,
                        headers={
                            "x-hub-signature-256": _signature(body, secret),
                            "x-github-delivery": "delivery-blocked-1",
                            "x-github-event": "pull_request",
                        },
                    )

    assert response.status_code == 202
    send.assert_not_called()
    mock_mark.assert_called_once()
    assert mock_mark.call_args.kwargs["delivery_id"] == "delivery-blocked-1"


def test_github_webhook_synchronize_debounce_coalesced_marks_delivery_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: Coalesced synchronize webhook acknowledges 202 and marks delivery processed."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY

    with patch.object(
        github_webhook_module,
        "synchronize_debounce_allows_enqueue",
        return_value=False,
    ):
        with patch.object(
            github_webhook_module,
            "mark_github_webhook_delivery_processed",
        ) as mock_mark:
            with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/webhooks/github",
                        content=body,
                        headers={
                            "x-hub-signature-256": _signature(body, secret),
                            "x-github-delivery": "delivery-coalesced-1",
                            "x-github-event": "pull_request",
                        },
                    )

    assert response.status_code == 202
    send.assert_not_called()
    mock_mark.assert_called_once()
    assert mock_mark.call_args.kwargs["delivery_id"] == "delivery-coalesced-1"


def test_github_webhook_skip_completed_analysis_marks_delivery_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: Skipped completed analysis acknowledges 202 and marks delivery processed."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_OPENED_BODY

    with patch.object(
        github_webhook_module,
        "evaluate_pull_request_enqueue_dedupe",
        return_value=(True, {}),
    ):
        with patch.object(
            github_webhook_module,
            "mark_github_webhook_delivery_processed",
        ) as mock_mark:
            with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/webhooks/github",
                        content=body,
                        headers={
                            "x-hub-signature-256": _signature(body, secret),
                            "x-github-delivery": "delivery-skip-completed-1",
                            "x-github-event": "pull_request",
                        },
                    )

    assert response.status_code == 202
    send.assert_not_called()
    mock_mark.assert_called_once()
    assert mock_mark.call_args.kwargs["delivery_id"] == "delivery-skip-completed-1"


def test_github_webhook_installation_created_marks_delivery_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154: Successful installation persistence marks delivery processed."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    body = (
        b'{"action":"created","installation":{'
        b'"id":12345,"account":{"login":"acme","type":"Organization"}},'
        b'"repositories":[]}'
    )

    with patch.object(
        github_webhook_module,
        "mark_github_webhook_delivery_processed",
    ) as mock_mark:
        with patch("reviewgate.app.analysis.jobs.run_pr_analysis_stub.send") as send:
            with TestClient(create_app()) as client:
                response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "x-hub-signature-256": _signature(body, secret),
                        "x-github-delivery": "delivery-inst-created-1",
                        "x-github-event": "installation",
                    },
                )

    assert response.status_code == 202
    send.assert_not_called()
    mock_mark.assert_called_once()
    assert mock_mark.call_args.kwargs["delivery_id"] == "delivery-inst-created-1"


def test_github_webhook_redis_set_nx_failure_cleans_up_debounce_and_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154 / Copilot: Redis SET NX failure triggers debounce and claim cleanup."""

    import redis.exceptions

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY

    token = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token),
    ):
        with patch.object(
            github_webhook_module,
            "synchronize_debounce_allows_enqueue",
            side_effect=redis.exceptions.ConnectionError("Redis connection dropped on SET NX"),
        ):
            with patch.object(
                github_webhook_module,
                "release_synchronize_debounce",
            ) as mock_release_debounce:
                with patch.object(
                    github_webhook_module,
                    "release_github_webhook_delivery",
                ) as mock_release_claim:
                    with TestClient(create_app()) as client:
                        response = client.post(
                            "/webhooks/github",
                            content=body,
                            headers={
                                "x-hub-signature-256": _signature(body, secret),
                                "x-github-delivery": "delivery-set-nx-fail",
                                "x-github-event": "pull_request",
                            },
                        )

    assert response.status_code == 503
    mock_release_debounce.assert_called_once()
    assert mock_release_debounce.call_args.kwargs["delivery_id"] == "delivery-set-nx-fail"
    mock_release_claim.assert_called_once()
    assert mock_release_claim.call_args.kwargs["delivery_id"] == "delivery-set-nx-fail"
    assert mock_release_claim.call_args.kwargs["claim_token"] == token


def test_github_webhook_debounce_cleanup_failure_does_not_release_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154 / PR #168: If debounce cleanup fails after enqueue failure, DO NOT release PostgreSQL claim."""

    import redis.exceptions

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY

    calls: list[str] = []

    def _failing_debounce_release(*_a: object, **_k: object) -> bool:
        calls.append("release_debounce")
        raise redis.exceptions.ConnectionError("Redis failed during cleanup")

    def _claim_release(*_a: object, **_k: object) -> None:
        calls.append("release_claim")

    token = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token),
    ):
        with patch.object(
            github_webhook_module,
            "synchronize_debounce_allows_enqueue",
            return_value=True,
        ):
            with patch(
                "reviewgate.app.analysis.broker_install.RedisBroker",
                lambda **_: StubBroker(),
            ):
                with patch(
                    "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
                    side_effect=RuntimeError("send failed"),
                ):
                    with patch.object(
                        github_webhook_module,
                        "release_synchronize_debounce",
                        side_effect=_failing_debounce_release,
                    ):
                        with patch.object(
                            github_webhook_module,
                            "release_github_webhook_delivery",
                            side_effect=_claim_release,
                        ) as mock_release_claim:
                            with TestClient(create_app(), raise_server_exceptions=False) as client:
                                response = client.post(
                                    "/webhooks/github",
                                    content=body,
                                    headers={
                                        "x-hub-signature-256": _signature(body, secret),
                                        "x-github-delivery": "delivery-order-fail",
                                        "x-github-event": "pull_request",
                                    },
                                )

    assert response.status_code == 500
    assert calls == ["release_debounce"]
    mock_release_claim.assert_not_called()


def test_github_webhook_synchronize_owner_retry_after_failed_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154 / PR #168: E2E flow: D1 claims -> D1 owns debounce -> send fails -> debounce release fails ->

    D1 remains active -> same D1 retried -> existing Redis value is D1 ->
    D1 treated as owner retry -> send() called again -> completes.
    """

    import redis.exceptions

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY

    # Mock Redis client state to simulate real debounce slot across retries
    redis_state: dict[str, str] = {}

    class FakeRedis:
        def set(
            self,
            name: str,
            value: str | bytes,
            *,
            nx: bool = False,
            ex: int | None = None,
        ) -> bool | None:
            val_str = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            if nx and name in redis_state:
                return None
            redis_state[name] = val_str
            return True

        def get(self, name: str) -> str | None:
            return redis_state.get(name)

        def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
            raise redis.exceptions.ConnectionError("Redis cleanup network failure")

        def close(self) -> None:
            pass

    fake_redis = FakeRedis()
    monkeypatch.setattr(
        "reviewgate.app.analysis.synchronize_debounce.connect_redis",
        lambda _s: fake_redis,
    )

    # First attempt for D1:
    token1 = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token1),
    ):
        with patch.object(
            github_webhook_module,
            "release_github_webhook_delivery",
        ) as mock_release_claim:
            with patch(
                "reviewgate.app.analysis.broker_install.RedisBroker",
                lambda **_: StubBroker(),
            ):
                with patch(
                    "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
                    side_effect=RuntimeError("queue transient error"),
                ) as mock_send_1:
                    with TestClient(create_app(), raise_server_exceptions=False) as client:
                        resp1 = client.post(
                            "/webhooks/github",
                            content=body,
                            headers={
                                "x-hub-signature-256": _signature(body, secret),
                                "x-github-delivery": "D1",
                                "x-github-event": "pull_request",
                            },
                        )

    assert resp1.status_code == 500
    mock_send_1.assert_called_once()
    # Debounce release failed in fake_redis.eval, so claim must NOT have been released
    mock_release_claim.assert_not_called()

    # Immediate GitHub retry while D1 is active returns 503
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("active", None),
    ):
        with TestClient(create_app()) as client:
            resp_active = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-hub-signature-256": _signature(body, secret),
                    "x-github-delivery": "D1",
                    "x-github-event": "pull_request",
                },
            )
    assert resp_active.status_code == 503

    # Subsequent retry after lease expiration: D1 reclaims PG, finds existing D1 in Redis debounce slot
    token2 = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token2),
    ):
        with patch.object(
            github_webhook_module,
            "mark_github_webhook_delivery_processed",
        ) as mock_mark:
            with patch(
                "reviewgate.app.analysis.broker_install.RedisBroker",
                lambda **_: StubBroker(),
            ):
                with patch(
                    "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
                ) as mock_send_2:
                    with TestClient(create_app()) as client:
                        resp2 = client.post(
                            "/webhooks/github",
                            content=body,
                            headers={
                                "x-hub-signature-256": _signature(body, secret),
                                "x-github-delivery": "D1",
                                "x-github-event": "pull_request",
                            },
                        )

    assert resp2.status_code == 202
    mock_send_2.assert_called_once()
    mock_mark.assert_called_once()
    assert mock_mark.call_args.kwargs["delivery_id"] == "D1"
    assert mock_mark.call_args.kwargs["claim_token"] == token2


def test_github_webhook_synchronize_debounce_d1_owns_d2_coalesced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154 / PR #168: D1 owns debounce slot -> D2 attempts synchronize -> D2 is genuinely coalesced."""
    from reviewgate.app.analysis.synchronize_debounce import synchronize_debounce_key

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY

    key = synchronize_debounce_key(owner="leo-aa88", repo="reviewgate", pull_number=1)
    redis_state: dict[str, str] = {key: "D1"}

    class FakeRedis:
        def set(
            self,
            name: str,
            value: str | bytes,
            *,
            nx: bool = False,
            ex: int | None = None,
        ) -> bool | None:
            if nx and name in redis_state:
                return None
            redis_state[name] = str(value)
            return True

        def get(self, name: str) -> str | None:
            return redis_state.get(name)

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "reviewgate.app.analysis.synchronize_debounce.connect_redis",
        lambda _s: FakeRedis(),
    )

    token_d2 = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token_d2),
    ):
        with patch.object(
            github_webhook_module,
            "mark_github_webhook_delivery_processed",
        ) as mock_mark:
            with patch(
                "reviewgate.app.analysis.jobs.run_pr_analysis_stub.send",
            ) as mock_send:
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/webhooks/github",
                        content=body,
                        headers={
                            "x-hub-signature-256": _signature(body, secret),
                            "x-github-delivery": "D2",
                            "x-github-event": "pull_request",
                        },
                    )

    assert response.status_code == 202
    mock_send.assert_not_called()
    mock_mark.assert_called_once()
    assert mock_mark.call_args.kwargs["delivery_id"] == "D2"
    assert mock_mark.call_args.kwargs["claim_token"] == token_d2


def test_github_webhook_actor_import_failure_cleans_up_debounce_and_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #154 / Copilot: Actor import failure triggers debounce and claim cleanup."""

    secret = "whsec"
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    body = _PR_SYNCHRONIZE_BODY

    token = uuid.uuid4()
    with patch.object(
        github_webhook_module,
        "claim_github_webhook_delivery",
        return_value=("claimed", token),
    ):
        with patch.object(
            github_webhook_module,
            "synchronize_debounce_allows_enqueue",
            return_value=True,
        ):
            with patch(
                "reviewgate.app.analysis.broker_install.RedisBroker",
                lambda **_: StubBroker(),
            ):
                with patch.object(
                    github_webhook_module,
                    "release_synchronize_debounce",
                ) as mock_release_debounce:
                    with patch.object(
                        github_webhook_module,
                        "release_github_webhook_delivery",
                    ) as mock_release_claim:
                        with patch.dict("sys.modules", {"reviewgate.app.analysis.jobs": None}):
                            with TestClient(create_app(), raise_server_exceptions=False) as client:
                                response = client.post(
                                    "/webhooks/github",
                                    content=body,
                                    headers={
                                        "x-hub-signature-256": _signature(body, secret),
                                        "x-github-delivery": "delivery-import-fail",
                                        "x-github-event": "pull_request",
                                    },
                                )

    assert response.status_code == 500
    mock_release_debounce.assert_called_once()
    assert mock_release_debounce.call_args.kwargs["delivery_id"] == "delivery-import-fail"
    mock_release_claim.assert_called_once()
    assert mock_release_claim.call_args.kwargs["delivery_id"] == "delivery-import-fail"
    assert mock_release_claim.call_args.kwargs["claim_token"] == token
