"""§24.3 GitHub REST integration coverage with mocked HTTP (issue #56).

Maps ``docs/DESIGN.md`` §24.3 scenarios to tests:

- Webhook signature valid / invalid / duplicate delivery / ``pull_request`` opened:
  ``tests/test_webhooks_github.py`` (for example
  ``test_github_webhook_accepts_valid_signature_and_enqueues``,
  ``test_github_webhook_rejects_bad_signature``,
  ``test_github_webhook_duplicate_delivery_returns_202_without_enqueue``).
- ``pull_request`` synchronized (enqueue path):
  :func:`test_webhook_pull_request_synchronize_enqueues_when_debounce_allows`.
- Config missing / present at GitHub:
  :func:`test_pipeline_config_missing_via_contents_404` and
  :func:`test_pipeline_config_present_via_contents_yml`.
- Comment create / update, labels, status check:
  :func:`test_publish_integration_creates_issue_comment` and
  :func:`test_publish_integration_updates_existing_bot_comment`.

Runs in CI without real GitHub credentials (``httpx.MockTransport``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from typing import Any, Final
from urllib.parse import unquote
from unittest.mock import patch

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr

pytest.importorskip("fastapi")

import dramatiq
from dramatiq.brokers.stub import StubBroker

import reviewgate.app.webhooks.github as github_webhook_module
from reviewgate.app.analysis import broker_install
from reviewgate.app.analysis.config_hash import compute_config_hash_from_yaml
from reviewgate.app.analysis.hosted_github_outputs import publish_hosted_pr_github_feedback
from reviewgate.app.analysis.pipeline import HostRepoContext, run_pr_analysis_for_natural_key
from reviewgate.app.github.comments import REVIEWGATE_REPORT_MARKER
from reviewgate.app.main import create_app
from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.repositories import AnalysisNaturalKey
from reviewgate.core.config import ReviewGateConfig
from reviewgate.core.report import suggested_labels
from reviewgate.core.schemas import ReviewabilityReport

_PR_SYNCHRONIZE_BODY = (
    b'{"action":"synchronize","number":1,'
    b'"installation":{"id":1111},'
    b'"repository":{"id":2222,"name":"reviewgate","owner":{"login":"leo-aa88"}}}'
)

_HEAD_SHA: Final[str] = "a1" + "c3e5" * 9  # 40 hex chars
_OWNER: Final[str] = "demo-owner"
_REPO: Final[str] = "demo-repo"
_INSTALL_ID: Final[int] = 4242
_PULL_NUMBER: Final[int] = 7
_BOT_LOGIN: Final[str] = "reviewgate-test[bot]"

_TEST_GITHUB_APP_PEM: Final[str] = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    .decode("ascii")
)


def _github_app_settings(*, bot_login: str | None = None) -> AppSettings:
    return AppSettings(
        github_app_id=100_001,
        github_app_private_key=SecretStr(_TEST_GITHUB_APP_PEM),
        **({"github_app_bot_login": bot_login} if bot_login else {}),
    )


def _signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _pull_request_json(*, head_sha: str) -> dict[str, Any]:
    return {
        "title": "Demo PR",
        "body": "Body",
        "user": {"login": "contributor"},
        "base": {"ref": "main"},
        "head": {"ref": "feature", "sha": head_sha},
        "additions": 4,
        "deletions": 2,
        "changed_files": 2,
    }


def _pull_files_json() -> list[dict[str, Any]]:
    return [
        {
            "filename": "src/a.py",
            "status": "modified",
            "additions": 2,
            "deletions": 1,
            "changes": 3,
        },
        {
            "filename": "README.md",
            "status": "added",
            "additions": 2,
            "deletions": 0,
            "changes": 2,
        },
    ]


def _access_token_response() -> dict[str, str]:
    return {
        "token": "ghs_integration_mock",
        "expires_at": "2099-01-01T00:00:00Z",
    }


def _pipeline_handler(
    *,
    head_sha: str,
    config_contents: httpx.Response | None,
) -> httpx.MockTransport:
    """Mock GitHub for :func:`run_pr_analysis_for_natural_key` (token, PR, files, YAML)."""

    token_path = f"/app/installations/{_INSTALL_ID}/access_tokens"
    pulls_path = f"/repos/{_OWNER}/{_REPO}/pulls/1"
    files_path = f"/repos/{_OWNER}/{_REPO}/pulls/1/files"
    yml_path = f"/repos/{_OWNER}/{_REPO}/contents/.reviewgate.yml"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == token_path:
            return httpx.Response(201, json=_access_token_response())
        if request.method == "GET" and request.url.path == pulls_path:
            return httpx.Response(200, json=_pull_request_json(head_sha=head_sha))
        if request.method == "GET" and request.url.path == files_path:
            return httpx.Response(200, json=_pull_files_json())
        if request.method == "GET" and request.url.path == yml_path:
            if config_contents is not None:
                return config_contents
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unmocked GitHub request: {request.method} {request.url!r}")

    return httpx.MockTransport(handler)


def test_pipeline_config_missing_via_contents_404() -> None:
    """§24.3 *config missing*: ``GET …/contents/.reviewgate.yml`` → 404 → defaults."""

    digest_default, _ = compute_config_hash_from_yaml(None)
    key = AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=1,
        head_sha=_HEAD_SHA,
        config_hash=digest_default,
        pr_metadata_hash="meta",
    )
    ctx = HostRepoContext(
        github_installation_id=_INSTALL_ID,
        owner=_OWNER,
        name=_REPO,
    )
    settings = _github_app_settings()
    transport = _pipeline_handler(head_sha=_HEAD_SHA, config_contents=None)
    with httpx.Client(transport=transport) as client:
        report, cfg, _artifacts = run_pr_analysis_for_natural_key(
            settings,
            key,
            ctx,
            http_client=client,
        )
    assert cfg == ReviewGateConfig()
    assert report.reviewability == "WARN"
    assert report.stats["files_changed"] == 2
    assert report.stats["human_loc_changed"] == 6
    codes = {w.code for w in report.warnings}
    assert "weak_pr_body" in codes
    assert "missing_linked_issue" in codes


def test_pipeline_config_present_via_contents_yml() -> None:
    """§24.3 *config exists*: contents API returns YAML; effective ``mode`` is applied."""

    yaml_text = "mode: app\n"
    digest, _ = compute_config_hash_from_yaml(yaml_text)
    enc = base64.b64encode(yaml_text.encode("utf-8")).decode("ascii")
    contents_ok = httpx.Response(
        200,
        json={
            "type": "file",
            "encoding": "base64",
            "content": enc,
        },
    )
    key = AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=1,
        head_sha=_HEAD_SHA,
        config_hash=digest,
        pr_metadata_hash="meta",
    )
    ctx = HostRepoContext(
        github_installation_id=_INSTALL_ID,
        owner=_OWNER,
        name=_REPO,
    )
    settings = _github_app_settings()
    transport = _pipeline_handler(head_sha=_HEAD_SHA, config_contents=contents_ok)
    with httpx.Client(transport=transport) as client:
        _report, cfg, _artifacts = run_pr_analysis_for_natural_key(
            settings,
            key,
            ctx,
            http_client=client,
        )
    assert cfg.mode == "app"


def _publish_handler(
    *,
    issue_comment_list: list[dict[str, Any]],
) -> tuple[httpx.MockTransport, list[tuple[str, str]]]:
    """Mock GitHub for :func:`publish_hosted_pr_github_feedback` (full REST surface)."""

    calls: list[tuple[str, str]] = []
    token_path = f"/app/installations/{_INSTALL_ID}/access_tokens"
    check_path = f"/repos/{_OWNER}/{_REPO}/check-runs"
    issue_labels_path = f"/repos/{_OWNER}/{_REPO}/issues/{_PULL_NUMBER}/labels"
    issue_comments_path = f"/repos/{_OWNER}/{_REPO}/issues/{_PULL_NUMBER}/comments"
    labels_base = f"/repos/{_OWNER}/{_REPO}/labels"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if request.method == "POST" and path == token_path:
            return httpx.Response(201, json=_access_token_response())
        if request.method == "POST" and path == check_path:
            return httpx.Response(201, json={"id": 9001})
        if request.method == "GET" and path.startswith(f"{labels_base}/"):
            suffix = path.removeprefix(f"{labels_base}/")
            name = unquote(suffix)
            return httpx.Response(200, json={"name": name, "color": "ededed"})
        if request.method == "GET" and path == issue_labels_path:
            return httpx.Response(200, json=[])
        if request.method == "POST" and path == issue_labels_path:
            return httpx.Response(200, json=[])
        if request.method == "GET" and path == issue_comments_path:
            return httpx.Response(200, json=issue_comment_list)
        if request.method == "POST" and path == issue_comments_path:
            return httpx.Response(201, json={"id": 88001})
        if request.method == "PATCH" and path.startswith("/repos/") and "/issues/comments/" in path:
            return httpx.Response(200, json={"id": 88002})
        raise AssertionError(f"unmocked GitHub request: {request.method} {request.url!r}")

    return httpx.MockTransport(handler), calls


def test_publish_integration_creates_issue_comment() -> None:
    """§24.3 *status check*, *labels*, *comment created* via one mocked publish pass."""

    settings = _github_app_settings(bot_login=_BOT_LOGIN)
    transport, calls = _publish_handler(issue_comment_list=[])
    key = AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=_PULL_NUMBER,
        head_sha=_HEAD_SHA,
        config_hash="x",
        pr_metadata_hash="y",
    )
    ctx = HostRepoContext(
        github_installation_id=_INSTALL_ID,
        owner=_OWNER,
        name=_REPO,
    )
    report = ReviewabilityReport(
        reviewability="PASS",
        stats={"files_changed": 2, "raw_loc_changed": 6, "human_loc_changed": 6},
        warnings=[],
        suggested_labels=suggested_labels("PASS", [], ReviewGateConfig().labels),
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )
    with httpx.Client(transport=transport) as client:
        publish_hosted_pr_github_feedback(
            settings,
            ctx=ctx,
            key=key,
            report=report,
            config=ReviewGateConfig(mode="app"),
            http_client=client,
        )

    issue_labels = f"/repos/{_OWNER}/{_REPO}/issues/{_PULL_NUMBER}/labels"
    assert any(p.endswith("/check-runs") for _, p in calls)
    assert any(p == issue_labels for _, p in calls)
    assert any(m == "POST" and p.endswith("/comments") for m, p in calls)
    assert not any(m == "PATCH" and "/issues/comments/" in p for m, p in calls)
    assert calls


def test_publish_integration_updates_existing_bot_comment() -> None:
    """§24.3 *comment updated* when the bot already left a marked comment."""

    settings = _github_app_settings(bot_login=_BOT_LOGIN)
    existing = [
        {
            "id": 77,
            "user": {"login": _BOT_LOGIN},
            "body": f"{REVIEWGATE_REPORT_MARKER}\n\nold markdown",
        },
    ]
    transport, calls = _publish_handler(issue_comment_list=existing)
    key = AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=_PULL_NUMBER,
        head_sha=_HEAD_SHA,
        config_hash="x",
        pr_metadata_hash="y",
    )
    ctx = HostRepoContext(
        github_installation_id=_INSTALL_ID,
        owner=_OWNER,
        name=_REPO,
    )
    report = ReviewabilityReport(
        reviewability="WARN",
        stats={"files_changed": 1, "raw_loc_changed": 2, "human_loc_changed": 2},
        warnings=[],
        suggested_labels=suggested_labels("WARN", [], ReviewGateConfig().labels),
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )
    with httpx.Client(transport=transport) as client:
        publish_hosted_pr_github_feedback(
            settings,
            ctx=ctx,
            key=key,
            report=report,
            config=ReviewGateConfig(mode="app"),
            http_client=client,
        )

    patch_calls = [p for m, p in calls if m == "PATCH" and "/issues/comments/" in p]
    assert patch_calls
    post_comments = [p for m, p in calls if m == "POST" and p.endswith("/comments")]
    assert not post_comments


def test_webhook_pull_request_synchronize_enqueues_when_debounce_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§24.3 *PR synchronized*: valid delivery enqueues when debounce allows."""

    monkeypatch.setenv(
        "REVIEWGATE_DATABASE_URL",
        "postgresql://unused:unused@127.0.0.1:9/unused",
    )
    monkeypatch.setenv("REVIEWGATE_GITHUB_WEBHOOK_SECRET", "sync_secret")
    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setattr(
        github_webhook_module,
        "claim_github_webhook_delivery",
        lambda *_a, **_k: "claimed",
    )
    monkeypatch.setattr(
        github_webhook_module,
        "persist_installation_webhook_payload",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        github_webhook_module,
        "pull_request_may_enqueue",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        github_webhook_module,
        "synchronize_debounce_allows_enqueue",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        github_webhook_module,
        "evaluate_pull_request_enqueue_dedupe",
        lambda *_a, **_k: (False, {}),
    )

    broker_install._last_installed_redis_url = None
    dramatiq.set_broker(StubBroker())
    try:
        body = _PR_SYNCHRONIZE_BODY
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
                            "x-hub-signature-256": _signature(body, "sync_secret"),
                            "x-github-delivery": "delivery-sync-ok-1",
                            "x-github-event": "pull_request",
                        },
                    )
        assert response.status_code == 202
        send.assert_called_once()
        args, _kwargs = send.call_args
        assert args[0]["github_pull_request_action"] == "synchronize"
    finally:
        broker_install._last_installed_redis_url = None
        dramatiq.set_broker(StubBroker())
