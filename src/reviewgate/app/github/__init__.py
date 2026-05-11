"""GitHub App integration for the hosted ReviewGate service (``docs/DESIGN.md`` §13).

This package owns server-to-server authentication, REST clients, and PR
comment upsert helpers (``docs/DESIGN.md`` §13.8). The deterministic engine in
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

__all__ = [
    "GitHubAppAuthError",
    "GitHubRestError",
    "InstallationAccessToken",
    "REVIEWGATE_REPORT_MARKER",
    "UpsertCommentResult",
    "fetch_installation_access_token",
    "fetch_pull_request",
    "fetch_pull_request_files",
    "fetch_repository_text_file_contents",
    "format_reviewgate_report_body",
    "mint_github_app_jwt",
    "resolve_reviewgate_bot_login",
    "upsert_reviewgate_report_issue_comment",
]
