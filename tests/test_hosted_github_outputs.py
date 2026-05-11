"""Tests for :mod:`reviewgate.app.analysis.hosted_github_outputs` (issue #54)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import SecretStr

from reviewgate.app.analysis.hosted_github_outputs import publish_hosted_pr_github_feedback
from reviewgate.app.analysis.pipeline import HostRepoContext
from reviewgate.app.github.auth import InstallationAccessToken
from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.repositories import AnalysisNaturalKey
from reviewgate.core.config import DEFAULT_STATUS_CHECK_NAME, ReviewGateConfig
from reviewgate.core.report import suggested_labels
from reviewgate.core.schemas import ReviewabilityReport


def test_publish_skips_github_when_mode_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """``mode: action`` must not call installation token exchange."""

    import reviewgate.app.analysis.hosted_github_outputs as mod

    fetch = MagicMock(side_effect=AssertionError("token fetch must not run"))
    monkeypatch.setattr(mod, "fetch_installation_access_token", fetch)

    report = ReviewabilityReport(
        reviewability="PASS",
        stats={"files_changed": 0, "raw_loc_changed": 0, "human_loc_changed": 0},
        warnings=[],
        suggested_labels=suggested_labels("PASS", [], ReviewGateConfig().labels),
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )
    key = AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=3,
        head_sha="abcdef0" * 5 + "a",
        config_hash="c",
        pr_metadata_hash="m",
    )
    ctx = HostRepoContext(github_installation_id=1, owner="o", name="r")
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(500))) as client:
        publish_hosted_pr_github_feedback(
            AppSettings(),
            ctx=ctx,
            key=key,
            report=report,
            config=ReviewGateConfig(mode="action"),
            http_client=client,
        )


def test_publish_runs_check_when_mode_app(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404, json={"message": "unmocked"})

    def _fake_fetch(
        _settings: AppSettings,
        _iid: int,
        *,
        http_client: httpx.Client,
    ) -> InstallationAccessToken:
        del _settings, _iid, http_client
        return InstallationAccessToken(
            token=SecretStr("ghs_x"),
            expires_at=datetime.now(tz=UTC),
        )

    import reviewgate.app.analysis.hosted_github_outputs as mod

    monkeypatch.setattr(mod, "fetch_installation_access_token", _fake_fetch)
    monkeypatch.setattr(mod, "ensure_reviewgate_labels_exist", lambda *a, **k: None)
    monkeypatch.setattr(mod, "sync_reviewgate_labels_on_issue", lambda *a, **k: None)
    monkeypatch.setattr(
        mod,
        "upsert_reviewgate_report_issue_comment",
        lambda *a, **k: None,
    )
    monkeypatch.setenv("REVIEWGATE_GITHUB_APP_BOT_LOGIN", "app[bot]")

    report = ReviewabilityReport(
        reviewability="PASS",
        stats={"files_changed": 0, "raw_loc_changed": 0, "human_loc_changed": 0},
        warnings=[],
        suggested_labels=suggested_labels("PASS", [], ReviewGateConfig().labels),
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )
    key = AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=3,
        head_sha="abcdef0" * 5 + "a",
        config_hash="c",
        pr_metadata_hash="m",
    )
    ctx = HostRepoContext(github_installation_id=1, owner="o", name="r")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        publish_hosted_pr_github_feedback(
            AppSettings(),
            ctx=ctx,
            key=key,
            report=report,
            config=ReviewGateConfig(mode="app"),
            http_client=client,
        )

    assert any(p.endswith("/check-runs") for p in calls)


def test_publish_both_mode_posts_suffixed_check_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """``mode: both`` uses a distinct default check name (§14.1)."""

    check_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/check-runs"):
            check_payloads.append(json.loads(request.content.decode()))
            return httpx.Response(201, json={"id": 2})
        return httpx.Response(404, json={"message": "unmocked"})

    def _fake_fetch(
        _settings: AppSettings,
        _iid: int,
        *,
        http_client: httpx.Client,
    ) -> InstallationAccessToken:
        del _settings, _iid, http_client
        return InstallationAccessToken(
            token=SecretStr("ghs_x"),
            expires_at=datetime.now(tz=UTC),
        )

    import reviewgate.app.analysis.hosted_github_outputs as mod

    monkeypatch.setattr(mod, "fetch_installation_access_token", _fake_fetch)
    monkeypatch.setattr(mod, "ensure_reviewgate_labels_exist", lambda *a, **k: None)
    monkeypatch.setattr(mod, "sync_reviewgate_labels_on_issue", lambda *a, **k: None)
    monkeypatch.setattr(
        mod,
        "upsert_reviewgate_report_issue_comment",
        lambda *a, **k: None,
    )
    monkeypatch.setenv("REVIEWGATE_GITHUB_APP_BOT_LOGIN", "app[bot]")

    report = ReviewabilityReport(
        reviewability="PASS",
        stats={"files_changed": 0, "raw_loc_changed": 0, "human_loc_changed": 0},
        warnings=[],
        suggested_labels=suggested_labels("PASS", [], ReviewGateConfig().labels),
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )
    key = AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=2,
        head_sha="abcdef0" * 5 + "a",
        config_hash="c",
        pr_metadata_hash="m",
    )
    ctx = HostRepoContext(github_installation_id=1, owner="o", name="r")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        publish_hosted_pr_github_feedback(
            AppSettings(),
            ctx=ctx,
            key=key,
            report=report,
            config=ReviewGateConfig(mode="both"),
            http_client=client,
        )

    assert check_payloads
    assert check_payloads[0]["name"] == f"{DEFAULT_STATUS_CHECK_NAME} (hosted)"
