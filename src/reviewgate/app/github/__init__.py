"""GitHub App integration for the hosted ReviewGate service (``docs/DESIGN.md`` §13).

This package owns server-to-server authentication, REST clients, and PR
comment upsert, label sync, and Checks API helpers (§13.8–§13.10). The deterministic engine in
:mod:`reviewgate.core` must never import from here.
"""

from __future__ import annotations

from reviewgate.app.github.auth import (
    GitHubAppAuthError,
    InstallationAccessToken,
    fetch_installation_access_token,
    mint_github_app_jwt,
)
from reviewgate.app.github.client import (
    GitHubRestError,
    fetch_pull_request,
    fetch_pull_request_files,
    fetch_repository_text_file_contents,
)
from reviewgate.app.github.comments import (
    REVIEWGATE_REPORT_MARKER,
    UpsertCommentResult,
    format_reviewgate_report_body,
    resolve_reviewgate_bot_login,
    upsert_reviewgate_report_issue_comment,
)
from reviewgate.app.github.checks import (
    create_completed_reviewability_check_run,
    reviewability_check_conclusion,
)
from reviewgate.app.github.coexistence import (
    effective_hosted_status_check,
    hosted_github_outputs_enabled,
)
from reviewgate.app.github.labels import (
    ensure_reviewgate_labels_exist,
    list_issue_label_names,
    managed_label_names,
    sync_reviewgate_labels_on_issue,
)

__all__ = [
    "GitHubAppAuthError",
    "GitHubRestError",
    "InstallationAccessToken",
    "REVIEWGATE_REPORT_MARKER",
    "UpsertCommentResult",
    "create_completed_reviewability_check_run",
    "effective_hosted_status_check",
    "ensure_reviewgate_labels_exist",
    "fetch_installation_access_token",
    "hosted_github_outputs_enabled",
    "fetch_pull_request",
    "fetch_pull_request_files",
    "fetch_repository_text_file_contents",
    "format_reviewgate_report_body",
    "list_issue_label_names",
    "managed_label_names",
    "mint_github_app_jwt",
    "reviewability_check_conclusion",
    "resolve_reviewgate_bot_login",
    "sync_reviewgate_labels_on_issue",
    "upsert_reviewgate_report_issue_comment",
]
