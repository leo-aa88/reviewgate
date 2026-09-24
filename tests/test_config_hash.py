"""Tests for :mod:`reviewgate.app.analysis.config_hash` (issue #43)."""

from __future__ import annotations

import base64
from typing import Final

import httpx
import pytest
from pydantic import SecretStr

pytest.importorskip("httpx")

from reviewgate.app.analysis.config_hash import (
    compute_config_hash_from_yaml,
    config_hash_with_template,
    fetch_reviewgate_yml_and_config_hash,
)
from reviewgate.app.github.client import fetch_repository_text_file_contents

_TOKEN: Final[SecretStr] = SecretStr("ghs_token")


def test_compute_config_hash_stable_for_identical_yaml() -> None:
    yaml = "version: 1\nmode: app\n"
    h1, r1 = compute_config_hash_from_yaml(yaml)
    h2, r2 = compute_config_hash_from_yaml(yaml)
    assert h1 == h2
    assert r1.config.mode == r2.config.mode


def test_compute_config_hash_same_for_malformed_yaml_defaults() -> None:
    """Two invalid documents that both fall back to defaults share a hash."""

    h1, _ = compute_config_hash_from_yaml("not: [")
    h2, _ = compute_config_hash_from_yaml("also: bad: yaml: [[")
    assert h1 == h2


def test_fetch_reviewgate_yml_and_config_hash_missing_file_uses_defaults() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/contents/.reviewgate.yml" in str(request.url)
        assert request.url.params.get("ref") == "main"
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        digest, result = fetch_reviewgate_yml_and_config_hash(
            _TOKEN,
            owner="o",
            repo="r",
            base_ref="main",
            http_client=client,
        )
    digest2, result2 = compute_config_hash_from_yaml(None)
    assert digest == digest2
    assert result.warnings == result2.warnings


def test_fetch_repository_text_file_contents_decodes_base64() -> None:
    text = "version: 1\nmode: both\n"
    b64 = base64.b64encode(text.encode()).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": b64,
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        got = fetch_repository_text_file_contents(
            _TOKEN,
            owner="o",
            repo="r",
            path=".reviewgate.yml",
            git_ref="main",
            http_client=client,
        )
    assert got == text


def test_fetch_reviewgate_yml_rejects_empty_base_ref() -> None:
    with pytest.raises(ValueError, match="base_ref"):
        fetch_reviewgate_yml_and_config_hash(
            _TOKEN,
            owner="o",
            repo="r",
            base_ref="   ",
        )


def test_template_content_is_an_authoritative_identity_input() -> None:
    from uuid import UUID
    from reviewgate.app.analysis.cache import analysis_cache_key

    old = config_hash_with_template("cfg", "## Testing", enabled=True)
    new = config_hash_with_template("cfg", "## Security", enabled=True)
    missing = config_hash_with_template("cfg", None, enabled=True)
    assert old != new != missing
    assert old != missing
    assert config_hash_with_template("cfg", "## Security", enabled=False) == "cfg"
    parts = dict(
        repository_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        pull_number=17,
        head_sha="same-head",
        pr_metadata_hash="same-body",
    )
    assert analysis_cache_key(**parts, config_hash=old) != analysis_cache_key(
        **parts,
        config_hash=new,
    )


def test_template_identity_is_stable_for_exact_snapshot() -> None:
    first = config_hash_with_template("cfg", "## Testing\n", enabled=True)
    second = config_hash_with_template("cfg", "## Testing\n", enabled=True)
    whitespace_change = config_hash_with_template("cfg", "## Testing", enabled=True)

    assert first == second
    assert first != whitespace_change


def test_enqueue_changed_template_does_not_reuse_completed_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from reviewgate.app.webhooks import enqueue_analysis_dedupe as enqueue
    from reviewgate.core.config import load_config
    from reviewgate.app.settings import AppSettings

    repository_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def scalar(self, _query):
            return repository_id

    monkeypatch.setattr(enqueue, "create_engine_from_settings", lambda *_: object())
    monkeypatch.setattr(enqueue, "create_session_factory", lambda *_: FakeSession)
    monkeypatch.setattr(
        enqueue, "fetch_installation_access_token", lambda *_a, **_kw: SimpleNamespace(token=_TOKEN)
    )
    revisions = iter(("base-before", "base-after"))
    monkeypatch.setattr(
        enqueue,
        "fetch_pull_request",
        lambda *_a, **_kw: {"base": {"ref": "main", "sha": next(revisions)}},
    )
    monkeypatch.setattr(
        enqueue,
        "fetch_reviewgate_yml_and_config_hash",
        lambda *_a, **_kw: ("cfg", load_config("policy:\n  require_pr_template: true\n")),
    )
    template = ["## Testing", "## Security"]
    monkeypatch.setattr(
        enqueue,
        "fetch_repository_text_file_contents",
        lambda *_a, **_kw: template.pop(0),
    )
    old_hash = config_hash_with_template("cfg", "## Testing", enabled=True)
    completed = MagicMock(side_effect=lambda _session, key: key.config_hash == old_hash)
    monkeypatch.setattr(enqueue, "completed_analysis_exists_for_key", completed)
    # The fake HTTP client never sends requests: every GitHub call is mocked.
    monkeypatch.setattr(enqueue.httpx, "Client", lambda **_kw: MagicMock())
    monkeypatch.setattr(enqueue, "compute_pr_metadata_hash", lambda **_kw: "same-meta")
    payload = {
        "installation": {"id": 1},
        "repository": {"id": 2, "owner": {"login": "owner"}, "name": "repo"},
        "pull_request": {
            "number": 17,
            "title": "Same title",
            "body": "Same body",
            "head": {"sha": "same-head"},
            "base": {"ref": "main", "sha": "base-snapshot"},
        },
    }
    settings = AppSettings(
        github_app_id=1,
        github_app_private_key="unused-test-key",
    )
    first_skip, _ = enqueue.evaluate_pull_request_enqueue_dedupe(settings, payload)
    second_skip, fields = enqueue.evaluate_pull_request_enqueue_dedupe(settings, payload)
    assert first_skip is True
    assert second_skip is False
    assert fields["reviewgate_config_hash"] != old_hash
    assert fields["reviewgate_template_identity_v2"] is True
    assert completed.call_count == 2


def test_worker_stale_template_job_never_reads_cache_or_completed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid
    from contextlib import nullcontext
    from unittest.mock import MagicMock
    import reviewgate.app.analysis.jobs as jobs
    from reviewgate.app.analysis.pipeline import AnalysisPipelineUserError, HostRepoContext

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setenv("REVIEWGATE_REDIS_URL", "redis://127.0.0.1:9/0")
    monkeypatch.setattr(jobs, "create_engine_from_settings", lambda *_: object())
    monkeypatch.setattr(jobs, "create_session_factory", lambda *_: FakeSession)
    monkeypatch.setattr(jobs, "worker_job_lock_hold", lambda *_a: nullcontext(True))
    monkeypatch.setattr(
        jobs,
        "resolve_host_repo_context",
        lambda *_a: HostRepoContext(1, "owner", "repo"),
    )
    monkeypatch.setattr(
        jobs,
        "validate_current_analysis_identity",
        MagicMock(
            side_effect=AnalysisPipelineUserError(
                "template changed",
                error_code="config_hash_mismatch",
            )
        ),
    )
    cache = MagicMock()
    begin = MagicMock()
    monkeypatch.setattr(jobs, "get_cached_final_report", cache)
    monkeypatch.setattr(jobs, "begin_analysis_for_job_start", begin)
    jobs.run_pr_analysis_stub(
        {
            "reviewgate_repository_id": str(uuid.uuid4()),
            "reviewgate_pull_number": 17,
            "reviewgate_head_sha": "sha",
            "reviewgate_config_hash": "old-identity",
            "reviewgate_pr_metadata_hash": "same-meta",
            "reviewgate_template_identity_v2": True,
        }
    )
    cache.assert_not_called()
    begin.assert_not_called()


def test_pipeline_rejects_changed_template_before_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import reviewgate.app.analysis.pipeline as pipeline
    from reviewgate.app.analysis.pipeline import AnalysisPipelineUserError, HostRepoContext
    from reviewgate.app.storage.repositories import AnalysisNaturalKey
    from reviewgate.core.config import load_config
    from reviewgate.app.settings import AppSettings

    monkeypatch.setattr(
        pipeline,
        "fetch_installation_access_token",
        lambda *_a, **_kw: SimpleNamespace(token=_TOKEN),
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_pull_request",
        lambda *_a, **_kw: {
            "head": {"sha": "same-head", "ref": "feat"},
            "base": {"sha": "base-snapshot", "ref": "main"},
            "title": "Same title",
            "body": "Same body",
            "changed_files": 1,
        },
    )
    config_fetch = MagicMock(
        return_value=(
            "cfg",
            load_config("policy:\n  require_pr_template: true\n"),
        )
    )
    monkeypatch.setattr(pipeline, "fetch_reviewgate_yml_and_config_hash", config_fetch)
    monkeypatch.setattr(
        pipeline,
        "fetch_repository_text_file_contents",
        MagicMock(return_value="## Security"),
    )
    files = MagicMock()
    monkeypatch.setattr(pipeline, "fetch_pull_request_files", files)
    key = AnalysisNaturalKey(
        uuid.uuid4(),
        17,
        "same-head",
        config_hash_with_template("cfg", "## Testing", enabled=True),
        "same-meta",
    )
    with pytest.raises(AnalysisPipelineUserError, match="config hash changed"):
        pipeline.run_pr_analysis_for_natural_key(
            AppSettings(),
            key,
            HostRepoContext(1, "owner", "repo"),
            http_client=MagicMock(),
        )
    assert config_fetch.call_args.kwargs["base_ref"] == "base-snapshot"
    files.assert_not_called()
