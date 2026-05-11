"""Hosted worker analysis pipeline (``docs/DESIGN.md`` §15; issues #50, #54).

Normalizes GitHub pull request data, applies §22.3 file-count tiering, fetches
effective repository config, and runs :func:`reviewgate.core.engine.analyze`.
Patches are not passed into the deterministic engine by default (``docs/DESIGN.md``
§11.5 / §21.2).

:func:`run_pr_analysis_for_natural_key` returns ``(report, config, artifacts)``
so callers can run the optional hosted LLM stage (issues #57–#64) and apply
§14.1 ``mode`` when publishing GitHub feedback (issue #54).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import httpx

from reviewgate.app.analysis.config_hash import fetch_reviewgate_yml_and_config_hash
from reviewgate.app.analysis.pr_file_tiers import classify_changed_file_count
from reviewgate.app.github.auth import fetch_installation_access_token
from reviewgate.app.github.client import (
    GitHubRestError,
    fetch_pull_request,
    fetch_pull_request_files,
)
from reviewgate.app.settings import AppSettings
from reviewgate.core.config import Labels, Policy, ReviewGateConfig
from reviewgate.core.engine import analyze
from reviewgate.core.report import suggested_labels
from reviewgate.core.schemas import (
    ChangedFile,
    EngineInput,
    EngineWarning,
    FileStatus,
    PRRecord,
    Reviewability,
    ReviewabilityReport,
    WarningSeverity,
)
from reviewgate.core.size import SizeStats

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from reviewgate.app.storage.repositories import AnalysisNaturalKey

logger = logging.getLogger(__name__)

_GITHUB_FILE_STATUS_TO_ENGINE: Final[dict[str, FileStatus]] = {
    "added": "added",
    "modified": "modified",
    "removed": "removed",
    "renamed": "renamed",
    "copied": "modified",
    "changed": "modified",
}

_WARN_HUGE_PR: Final[str] = "huge_pr_changed_files"


@dataclass(frozen=True, slots=True)
class PipelineAnalysisArtifacts:
    """PR metadata and file list for hosted LLM packaging (§11.5).

    Absent only on §22.3 fail-fast paths where YAML and full file fetches are skipped.
    """

    pr: PRRecord
    files: list[ChangedFile]
    changed_files_count: int


class AnalysisPipelineUserError(ValueError):
    """Non-retriable pipeline failure (deterministic validation / drift)."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class HostRepoContext:
    """Repository and installation identifiers resolved from Postgres."""

    github_installation_id: int
    owner: str
    name: str


def resolve_host_repo_context(
    session: "Session",
    repository_id: uuid.UUID,
) -> HostRepoContext | None:
    """Load owner, short repo name, and installation id for GitHub REST calls."""

    from sqlalchemy import select

    from reviewgate.app.storage.models import Installation, Repository

    stmt = (
        select(Repository, Installation.github_installation_id)
        .join(Installation, Repository.installation_id == Installation.id)
        .where(Repository.id == repository_id)
    )
    row = session.execute(stmt).one_or_none()
    if row is None:
        return None
    repo_row, github_installation_id = row
    return HostRepoContext(
        github_installation_id=int(github_installation_id),
        owner=repo_row.owner,
        name=repo_row.name,
    )


def _pull_doc_to_pr_record(pr_doc: dict[str, Any]) -> PRRecord:
    """Map ``GET /pulls/{n}`` JSON to :class:`~reviewgate.core.schemas.PRRecord`."""

    user_obj = pr_doc.get("user")
    author = ""
    if isinstance(user_obj, dict):
        login = user_obj.get("login")
        if isinstance(login, str):
            author = login

    base_obj = pr_doc.get("base")
    head_obj = pr_doc.get("head")
    base_branch = ""
    head_branch = ""
    if isinstance(base_obj, dict):
        ref = base_obj.get("ref")
        if isinstance(ref, str):
            base_branch = ref
    if isinstance(head_obj, dict):
        ref = head_obj.get("ref")
        if isinstance(ref, str):
            head_branch = ref

    raw_title = pr_doc.get("title")
    title = raw_title if isinstance(raw_title, str) else ""
    raw_body = pr_doc.get("body")
    body = raw_body if isinstance(raw_body, str) else ""

    def _int_field(key: str) -> int:
        raw = pr_doc.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            return 0
        return raw

    return PRRecord(
        title=title,
        body=body,
        author=author,
        base_branch=base_branch,
        head_branch=head_branch,
        additions=_int_field("additions"),
        deletions=_int_field("deletions"),
        changed_files=_int_field("changed_files"),
    )


def _github_file_to_changed_file(
    item: dict[str, Any],
    *,
    include_patch: bool,
) -> ChangedFile:
    """Normalize a ``pulls/{n}/files`` entry to :class:`~reviewgate.core.schemas.ChangedFile`."""

    filename_raw = item.get("filename")
    filename = filename_raw if isinstance(filename_raw, str) else ""
    status_raw = item.get("status")
    status_key = status_raw if isinstance(status_raw, str) else "modified"
    engine_status = _GITHUB_FILE_STATUS_TO_ENGINE.get(status_key, "modified")

    def _int_field(key: str) -> int:
        raw = item.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            return 0
        return raw

    additions = _int_field("additions")
    deletions = _int_field("deletions")
    changes = _int_field("changes")
    if changes == 0:
        changes = additions + deletions

    patch_val: str | None = None
    if include_patch:
        raw_patch = item.get("patch")
        if isinstance(raw_patch, str) and raw_patch.strip():
            patch_val = raw_patch

    return ChangedFile(
        filename=filename,
        status=engine_status,
        additions=additions,
        deletions=deletions,
        changes=changes,
        patch=patch_val,
    )


def _fail_fast_report(
    pr_record: PRRecord,
    user_message: str,
    *,
    policy: Policy,
    labels: Labels,
) -> ReviewabilityReport:
    """Synthetic deterministic report for §22.3 fail-fast tier (>1000 files)."""

    raw = pr_record.additions + pr_record.deletions
    stats = SizeStats(
        raw_loc_changed=raw,
        excluded_loc_changed=0,
        human_loc_changed=raw,
        files_changed=pr_record.changed_files,
        additions=pr_record.additions,
        deletions=pr_record.deletions,
    )
    verdict: Reviewability = "FAIL" if policy.fail_on_huge_pr else "WARN"
    severity: WarningSeverity = "high" if policy.fail_on_huge_pr else "medium"
    warn = EngineWarning(
        code=_WARN_HUGE_PR,
        severity=severity,
        message=user_message,
        evidence={"changed_files": pr_record.changed_files},
    )
    return ReviewabilityReport(
        reviewability=verdict,
        stats=stats.model_dump(),
        warnings=[warn],
        suggested_labels=suggested_labels(verdict, [warn], labels),
        file_categories=[],
        split_hints=[],
        reviewer_checklist=[],
    )


def run_pr_analysis_for_natural_key(
    settings: AppSettings,
    key: "AnalysisNaturalKey",
    ctx: HostRepoContext,
    *,
    http_client: httpx.Client,
) -> tuple[ReviewabilityReport, ReviewGateConfig, PipelineAnalysisArtifacts | None]:
    """Fetch PR data from GitHub, build :class:`~reviewgate.core.schemas.EngineInput`, run core.

    Args:
        settings: Application settings (GitHub App credentials).
        key: Five-part natural key from the job envelope.
        ctx: Repository owner/name and installation id from Postgres.
        http_client: Shared HTTP client for GitHub calls.

    Returns:
        Tuple of the deterministic :class:`~reviewgate.core.schemas.ReviewabilityReport`,
        the effective :class:`~reviewgate.core.config.ReviewGateConfig` loaded from
        the repository (or defaults for §22.3 fail-fast before YAML is read), and
        optional :class:`PipelineAnalysisArtifacts` for hosted LLM input packaging
        (``None`` on fail-fast early return).

    Raises:
        GitHubRestError: On GitHub HTTP failures (respect ``retriable``).
        AnalysisPipelineUserError: On non-retriable validation failures.
    """

    access = fetch_installation_access_token(
        settings,
        ctx.github_installation_id,
        http_client=http_client,
    )
    pr_doc = fetch_pull_request(
        access.token,
        owner=ctx.owner,
        repo=ctx.name,
        pull_number=key.pull_number,
        http_client=http_client,
    )

    head_obj = pr_doc.get("head")
    if isinstance(head_obj, dict):
        sha_val = head_obj.get("sha")
        if isinstance(sha_val, str) and sha_val.strip():
            if sha_val.strip() != key.head_sha:
                msg = "PR head SHA changed since job was enqueued"
                raise AnalysisPipelineUserError(msg, error_code="head_sha_mismatch")

    pr_record = _pull_doc_to_pr_record(pr_doc)

    base_obj = pr_doc.get("base")
    base_ref = ""
    if isinstance(base_obj, dict):
        ref = base_obj.get("ref")
        if isinstance(ref, str):
            base_ref = ref.strip()
    if not base_ref:
        msg = "pull request JSON missing base ref"
        raise AnalysisPipelineUserError(msg, error_code="invalid_pr_payload")

    digest, load_result = fetch_reviewgate_yml_and_config_hash(
        access.token,
        owner=ctx.owner,
        repo=ctx.name,
        base_ref=base_ref,
        http_client=http_client,
    )
    if digest != key.config_hash:
        logger.warning(
            "config_hash mismatch for analysis (repo=%s/%s pr=%s stored=%s fetched=%s)",
            ctx.owner,
            ctx.name,
            key.pull_number,
            key.config_hash,
            digest,
        )
        msg = "effective config hash changed since job was enqueued"
        raise AnalysisPipelineUserError(msg, error_code="config_hash_mismatch")

    tier_cls = classify_changed_file_count(pr_record.changed_files)
    if tier_cls.tier == "fail_fast":
        return (
            _fail_fast_report(
                pr_record,
                tier_cls.fail_fast_message or "",
                policy=load_result.config.policy,
                labels=load_result.config.labels,
            ),
            load_result.config,
            None,
        )

    files_raw = fetch_pull_request_files(
        access.token,
        owner=ctx.owner,
        repo=ctx.name,
        pull_number=key.pull_number,
        http_client=http_client,
    )
    changed_files = [
        _github_file_to_changed_file(f, include_patch=False) for f in files_raw
    ]

    engine_input = EngineInput(
        pr=pr_record,
        files=changed_files,
        config=load_result.config.model_dump(mode="json"),
    )
    artifacts = PipelineAnalysisArtifacts(
        pr=pr_record,
        files=changed_files,
        changed_files_count=pr_record.changed_files,
    )
    return analyze(engine_input), load_result.config, artifacts
