"""GitHub App integration for the hosted ReviewGate service (``docs/DESIGN.md`` §13).

This package owns server-to-server authentication and (in later issues) REST
clients. The deterministic engine in :mod:`reviewgate.core` must never import
from here.
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

__all__ = [
    "GitHubAppAuthError",
    "GitHubRestError",
    "InstallationAccessToken",
    "fetch_installation_access_token",
    "fetch_pull_request",
    "fetch_pull_request_files",
    "fetch_repository_text_file_contents",
    "mint_github_app_jwt",
]
