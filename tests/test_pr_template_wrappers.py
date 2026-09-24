"""Wrapper regression coverage for issue #170."""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

import reviewgate.app.analysis.pipeline as pipeline
from reviewgate.app.analysis.config_hash import config_hash_with_template
from reviewgate.app.analysis.pipeline import HostRepoContext
from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.repositories import AnalysisNaturalKey
from reviewgate.core.config import load_config
from reviewgate_action import pr_template_fetch, run_core

TEMPLATE = "## Why\n\n<!-- Explain the change. -->\n"


def test_action_fetches_base_branch_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []

    def fake_get(url, *, token, opener, missing_ok):
        requests.append((url, token, missing_ok))
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(TEMPLATE.encode()).decode(),
        }, {}

    monkeypatch.setattr(pr_template_fetch, "_http_get_json", fake_get)
    assert (
        pr_template_fetch.fetch_pr_template(
            token="token", repo_slug="owner/repo", base_ref="release/2026"
        )
        == TEMPLATE
    )
    assert requests == [
        (
            "https://api.github.com/repos/owner/repo/contents/"
            ".github/PULL_REQUEST_TEMPLATE.md?ref=release%2F2026",
            "token",
            True,
        )
    ]


def test_absent_template_and_malformed_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = MagicMock(return_value=(None, {}))
    monkeypatch.setattr(pr_template_fetch, "_http_get_json", fetch)
    assert (
        pr_template_fetch.fetch_pr_template(token="token", repo_slug="owner/repo", base_ref="main")
        is None
    )

    fetch.return_value = (
        {"type": "file", "encoding": "base64", "content": "!!!"},
        {},
    )
    with pytest.raises(RuntimeError, match="base64"):
        pr_template_fetch.fetch_pr_template(token="token", repo_slug="owner/repo", base_ref="main")


def _input_file(tmp_path: Path) -> Path:
    payload = {
        "pr": {
            "title": "Fixes #170",
            "body": "Fixes #170.",
            "author": "octocat",
            "base_branch": "main",
            "head_branch": "feature",
            "additions": 0,
            "deletions": 0,
            "changed_files": 0,
        },
        "files": [],
        "config": {},
    }
    target = tmp_path / "input.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_action_only_fetches_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _input_file(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    fetch = MagicMock(return_value=TEMPLATE)
    monkeypatch.setattr(pr_template_fetch, "fetch_pr_template", fetch)
    args = [
        "--input",
        str(target),
        "--workspace",
        str(tmp_path),
        "--post-comment",
        "false",
        "--fail-on",
        "never",
    ]

    assert run_core.main(args) == 0
    capsys.readouterr()
    fetch.assert_not_called()

    (tmp_path / ".reviewgate.yml").write_text(
        "policy:\n  require_pr_template: true\n", encoding="utf-8"
    )
    assert run_core.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    fetch.assert_called_once_with(token="token", repo_slug="owner/repo", base_ref="main")
    assert any(item["code"] == "pr_template_not_followed" for item in result["warnings"])


def test_action_fetch_failure_does_not_silently_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _input_file(tmp_path)
    (tmp_path / ".reviewgate.yml").write_text(
        "policy:\n  require_pr_template: true\n", encoding="utf-8"
    )
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(
        pr_template_fetch,
        "fetch_pr_template",
        MagicMock(side_effect=RuntimeError("GitHub HTTP 503")),
    )
    assert (
        run_core.main(
            [
                "--input",
                str(target),
                "--workspace",
                str(tmp_path),
                "--post-comment",
                "false",
            ]
        )
        == 2
    )
    assert "GitHub HTTP 503" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("enabled", "template", "expected"),
    [(True, TEMPLATE, True), (True, None, False), (False, TEMPLATE, False)],
)
def test_hosted_fetches_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    template: str | None,
    expected: bool,
) -> None:
    key = AnalysisNaturalKey(
        repository_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        pull_number=17,
        head_sha="sha",
        config_hash=config_hash_with_template(
            "config-hash",
            template,
            enabled=enabled,
        ),
        pr_metadata_hash="metadata-hash",
    )
    ctx = HostRepoContext(github_installation_id=9001, owner="owner", name="repo")
    monkeypatch.setattr(
        pipeline,
        "fetch_installation_access_token",
        lambda *_a, **_kw: SimpleNamespace(token=SecretStr("token")),
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_pull_request",
        lambda *_a, **_kw: {
            "title": "Fixes #170",
            "body": "Fixes #170.",
            "additions": 0,
            "deletions": 0,
            "changed_files": 0,
            "user": {"login": "octocat"},
            "base": {"ref": "main"},
            "head": {"ref": "feature", "sha": "sha"},
        },
    )
    config = load_config("policy:\n  require_pr_template: true\n" if enabled else None)
    monkeypatch.setattr(
        pipeline,
        "fetch_reviewgate_yml_and_config_hash",
        lambda *_a, **_kw: ("config-hash", config),
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_pull_request_files",
        lambda *_a, **_kw: [],
    )
    fetch = MagicMock(return_value=template)
    monkeypatch.setattr(pipeline, "fetch_repository_text_file_contents", fetch)

    report, _cfg, _artifacts = pipeline.run_pr_analysis_for_natural_key(
        AppSettings(), key, ctx, http_client=MagicMock()
    )
    actual = any(warning.code == "pr_template_not_followed" for warning in report.warnings)
    assert actual is expected
    if enabled:
        fetch.assert_called_once()
        assert fetch.call_args.kwargs["git_ref"] == "main"
        assert fetch.call_args.kwargs["path"] == ".github/PULL_REQUEST_TEMPLATE.md"
    else:
        fetch.assert_not_called()
