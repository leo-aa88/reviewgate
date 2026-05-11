"""Post ReviewGate PR feedback to GitHub after a successful analysis (§14.1; issue #54).

Runs only when :func:`reviewgate.app.github.coexistence.hosted_github_outputs_enabled`
is true for the repository's effective config. Failures are logged and swallowed
so a GitHub permissions or configuration problem does not fail the Dramatiq job
after Postgres has already recorded the analysis.

Example:
    Called from the worker after ``session.commit()``::

        publish_hosted_pr_github_feedback(
            settings,
            ctx=host_ctx,
            key=natural_key,
            report=report,
            config=effective_config,
            http_client=http_client,
        )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from reviewgate.app.github.auth import GitHubAppAuthError, fetch_installation_access_token
from reviewgate.app.github.checks import create_completed_reviewability_check_run
from reviewgate.app.github.client import GitHubRestError
from reviewgate.app.github.coexistence import (
    effective_hosted_status_check,
    hosted_github_outputs_enabled,
)
from reviewgate.app.github.comments import (
    format_reviewgate_report_body,
    resolve_reviewgate_bot_login,
    upsert_reviewgate_report_issue_comment,
)
from reviewgate.app.github.labels import (
    ensure_reviewgate_labels_exist,
    sync_reviewgate_labels_on_issue,
)
from reviewgate.app.settings import AppSettings
from reviewgate.core.config import ReviewGateConfig
from reviewgate.core.schemas import ReviewabilityReport

if TYPE_CHECKING:
    from reviewgate.app.analysis.pipeline import HostRepoContext
    from reviewgate.app.storage.repositories import AnalysisNaturalKey

logger = logging.getLogger(__name__)


def _comment_markdown(report: ReviewabilityReport) -> str:
    lines = [
        f"## ReviewGate: {report.reviewability}",
        "",
    ]
    if report.warnings:
        lines.append("### Issues")
        for w in report.warnings:
            lines.append(f"- **{w.code}**: {w.message}")
    else:
        lines.append("No deterministic warnings.")
    return "\n".join(lines)


def publish_hosted_pr_github_feedback(
    settings: AppSettings,
    *,
    ctx: "HostRepoContext",
    key: "AnalysisNaturalKey",
    report: ReviewabilityReport,
    config: ReviewGateConfig,
    http_client: httpx.Client,
) -> None:
    """Publish check run, labels, and PR comment when ``mode`` allows (§14.1).

    Args:
        settings: Process settings (App id, keys, optional bot login).
        ctx: Repository owner/name and installation id.
        key: Natural key (PR number, ``head_sha``, etc.).
        report: Deterministic report from the pipeline.
        config: Effective ``.reviewgate.yml`` (``mode``, ``labels``, ``status_check``).
        http_client: Shared HTTP client for GitHub REST calls.
    """

    if not hosted_github_outputs_enabled(config):
        logger.info(
            "hosted_github_outputs_skipped",
            extra={"mode": config.mode},
        )
        return

    try:
        access = fetch_installation_access_token(
            settings,
            ctx.github_installation_id,
            http_client=http_client,
        )
    except (GitHubAppAuthError, GitHubRestError, httpx.HTTPError, ValueError, OSError) as exc:
        logger.warning("hosted_github_token_failed", exc_info=exc)
        return

    token = access.token
    status_check = effective_hosted_status_check(config)
    if status_check.enabled:
        try:
            create_completed_reviewability_check_run(
                token,
                owner=ctx.owner,
                repo=ctx.name,
                head_sha=key.head_sha,
                reviewability=report.reviewability,
                status_check=status_check,
                http_client=http_client,
            )
        except (GitHubRestError, httpx.HTTPError, ValueError, OSError) as exc:
            logger.warning("hosted_github_check_run_failed", exc_info=exc)

    try:
        ensure_reviewgate_labels_exist(
            token,
            owner=ctx.owner,
            repo=ctx.name,
            labels_config=config.labels,
            http_client=http_client,
        )
        sync_reviewgate_labels_on_issue(
            token,
            owner=ctx.owner,
            repo=ctx.name,
            issue_number=key.pull_number,
            desired_labels=report.suggested_labels,
            labels_config=config.labels,
            http_client=http_client,
        )
    except (GitHubRestError, httpx.HTTPError, ValueError, OSError) as exc:
        logger.warning("hosted_github_label_sync_failed", exc_info=exc)

    try:
        login = resolve_reviewgate_bot_login(settings)
    except ValueError:
        logger.info("hosted_github_comment_skipped_no_bot_login")
        return

    try:
        body = format_reviewgate_report_body(_comment_markdown(report))
        upsert_reviewgate_report_issue_comment(
            token,
            owner=ctx.owner,
            repo=ctx.name,
            issue_number=key.pull_number,
            body_markdown=body,
            bot_login=login,
            http_client=http_client,
        )
    except (GitHubRestError, httpx.HTTPError, ValueError, OSError) as exc:
        logger.warning("hosted_github_comment_upsert_failed", exc_info=exc)
