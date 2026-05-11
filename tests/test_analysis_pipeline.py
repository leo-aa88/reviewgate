"""Tests for :mod:`reviewgate.app.analysis.pipeline` (issue #50)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("httpx")

from reviewgate.app.analysis.pipeline import (
    AnalysisPipelineUserError,
    HostRepoContext,
    _fail_fast_report,
    _pull_doc_to_pr_record,
    run_pr_analysis_for_natural_key,
)
from reviewgate.core.config import Policy, ReviewGateConfig
from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.repositories import AnalysisNaturalKey


def test_pull_doc_to_pr_record_minimal() -> None:
    """GitHub pull JSON maps into :class:`~reviewgate.core.schemas.PRRecord`."""

    pr_doc: dict[str, Any] = {
        "title": "Add feature",
        "body": "Desc",
        "additions": 3,
        "deletions": 1,
        "changed_files": 2,
        "user": {"login": "alice"},
        "base": {"ref": "main"},
        "head": {"ref": "feat", "sha": "abc"},
    }
    rec = _pull_doc_to_pr_record(pr_doc)
    assert rec.title == "Add feature"
    assert rec.body == "Desc"
    assert rec.author == "alice"
    assert rec.base_branch == "main"
    assert rec.head_branch == "feat"
    assert rec.additions == 3
    assert rec.deletions == 1
    assert rec.changed_files == 2


def test_fail_fast_report_reviewability_fail() -> None:
    """§22.3 fail-fast tier yields a FAIL report with a single warning."""

    from reviewgate.app.analysis.pr_file_tiers import HUGE_PR_FAIL_FAST_MESSAGE

    pr = _pull_doc_to_pr_record(
        {
            "title": "x",
            "body": "",
            "additions": 0,
            "deletions": 0,
            "changed_files": 1200,
            "user": {"login": "u"},
            "base": {"ref": "main"},
            "head": {"ref": "h"},
        },
    )
    cfg = ReviewGateConfig()
    report = _fail_fast_report(
        pr,
        HUGE_PR_FAIL_FAST_MESSAGE,
        policy=cfg.policy,
        labels=cfg.labels,
    )
    assert report.reviewability == "FAIL"
    assert len(report.warnings) == 1
    assert report.warnings[0].code == "huge_pr_changed_files"


def test_fail_fast_report_softens_when_fail_on_huge_pr_disabled() -> None:
    """``policy.fail_on_huge_pr: false`` maps the §22.3 fail-fast tier to WARN."""

    from reviewgate.app.analysis.pr_file_tiers import HUGE_PR_FAIL_FAST_MESSAGE

    pr = _pull_doc_to_pr_record(
        {
            "title": "x",
            "body": "",
            "additions": 0,
            "deletions": 0,
            "changed_files": 1200,
            "user": {"login": "u"},
            "base": {"ref": "main"},
            "head": {"ref": "h"},
        },
    )
    policy = Policy(fail_on_huge_pr=False)
    report = _fail_fast_report(
        pr,
        HUGE_PR_FAIL_FAST_MESSAGE,
        policy=policy,
        labels=ReviewGateConfig().labels,
    )
    assert report.reviewability == "WARN"
    assert report.warnings[0].severity == "medium"


def test_run_pr_analysis_head_sha_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale head SHA vs GitHub raises :class:`AnalysisPipelineUserError`."""

    def _fake_token(
        _settings: AppSettings,
        _installation_id: int,
        *,
        http_client: object,
    ) -> object:
        from pydantic import SecretStr

        from reviewgate.app.github.auth import InstallationAccessToken

        return InstallationAccessToken(
            token=SecretStr("fake"),
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )

    def _fake_pr(
        _token: object,
        *,
        owner: str,
        repo: str,
        pull_number: int,
        http_client: object,
    ) -> dict[str, Any]:
        del owner, repo, pull_number, http_client
        return {
            "title": "t",
            "body": "",
            "additions": 0,
            "deletions": 0,
            "changed_files": 1,
            "user": {"login": "a"},
            "base": {"ref": "main"},
            "head": {"ref": "f", "sha": "deadbeef"},
        }

    monkeypatch.setattr(
        "reviewgate.app.analysis.pipeline.fetch_installation_access_token",
        _fake_token,
    )
    monkeypatch.setattr(
        "reviewgate.app.analysis.pipeline.fetch_pull_request",
        _fake_pr,
    )

    key = AnalysisNaturalKey(
        repository_id=uuid.uuid4(),
        pull_number=1,
        head_sha="aaa111",
        config_hash="x",
        pr_metadata_hash="y",
    )
    ctx = HostRepoContext(github_installation_id=1, owner="o", name="n")

    with pytest.raises(AnalysisPipelineUserError, match="head SHA"):
        run_pr_analysis_for_natural_key(
            AppSettings(),
            key,
            ctx,
            http_client=MagicMock(),
        )
