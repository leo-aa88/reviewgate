"""GitHub App JWT minting and installation access tokens (``docs/DESIGN.md`` §13.4).

GitHub Apps authenticate with a short-lived JSON Web Token (RS256) signed with
the app's private key, then exchange that JWT for an **installation access
token** scoped to a single installation. This module implements that exchange
only; REST reads live in :mod:`reviewgate.app.github.client` (issue #40).

Secrets loaded via :class:`~reviewgate.app.settings.AppSettings` are typed as
:class:`~pydantic.SecretStr` so they are redacted from logs and reprs; never
print raw tokens or PEM material.

Example:
    Minting a JWT and exchanging it (with a real HTTP client in production)::

        from reviewgate.app.github.auth import (
            fetch_installation_access_token,
            mint_github_app_jwt,
        )
        from reviewgate.app.settings import AppSettings

        settings = AppSettings()
        jwt_text = mint_github_app_jwt(settings)
        installation = fetch_installation_access_token(
            settings,
            installation_id=12345,
        )
        # Use installation.token.get_secret_value() only over TLS to GitHub.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import httpx
import jwt
from pydantic import SecretStr

from reviewgate.app.settings import AppSettings

_GITHUB_API_ORIGIN: Final[str] = "https://api.github.com"
_GITHUB_JWT_ALGORITHM: Final[str] = "RS256"
_GITHUB_JWT_MAX_TTL_SECONDS: Final[int] = 600
_GITHUB_JWT_CLOCK_SKEW_SECONDS: Final[int] = 60
GITHUB_JWT_EFFECTIVE_TTL_SECONDS: Final[int] = 540
_DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0
_GITHUB_ACCEPT_HEADER: Final[str] = "application/vnd.github+json"
_GITHUB_API_VERSION: Final[str] = "2022-11-28"


class GitHubAppAuthError(RuntimeError):
    """Raised when JWT creation or installation token exchange fails."""


@dataclass(frozen=True, slots=True)
class InstallationAccessToken:
    """Opaque installation token returned by GitHub's access token endpoint."""

    token: SecretStr
    expires_at: datetime


def _require_github_app_credentials(settings: AppSettings) -> tuple[int, str]:
    """Return app id and PEM material or raise :class:`GitHubAppAuthError`."""

    if settings.github_app_id is None:
        msg = "github_app_id is not configured (set REVIEWGATE_GITHUB_APP_ID)"
        raise GitHubAppAuthError(msg)
    key = settings.github_app_private_key
    if key is None:
        msg = (
            "github_app_private_key is not configured "
            "(set REVIEWGATE_GITHUB_APP_PRIVATE_KEY)"
        )
        raise GitHubAppAuthError(msg)
    pem = key.get_secret_value().strip()
    if not pem:
        msg = "github_app_private_key is empty"
        raise GitHubAppAuthError(msg)
    return settings.github_app_id, pem


def mint_github_app_jwt(
    settings: AppSettings,
    *,
    now: float | None = None,
) -> str:
    """Mint a short-lived GitHub App JWT (RS256) per GitHub documentation.

    Args:
        settings: Application settings containing app id and private key PEM.
        now: Optional Unix timestamp for deterministic tests; defaults to
            :func:`time.time`.

    Returns:
        Encoded JWT string suitable for ``Authorization: Bearer``.

    Raises:
        GitHubAppAuthError: If credentials are missing or signing fails.
    """

    app_id, pem = _require_github_app_credentials(settings)
    issued_at = int(now if now is not None else time.time())
    # PyJWT requires ``iss`` to be a string (GitHub documents the App ID here).
    payload = {
        "iat": issued_at - _GITHUB_JWT_CLOCK_SKEW_SECONDS,
        "exp": issued_at + GITHUB_JWT_EFFECTIVE_TTL_SECONDS,
        "iss": str(app_id),
    }
    if payload["exp"] - payload["iat"] > _GITHUB_JWT_MAX_TTL_SECONDS:
        msg = "internal error: JWT lifetime exceeds GitHub maximum"
        raise GitHubAppAuthError(msg)
    try:
        return jwt.encode(payload, pem, algorithm=_GITHUB_JWT_ALGORITHM)
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        msg = "failed to sign GitHub App JWT"
        raise GitHubAppAuthError(msg) from exc


def fetch_installation_access_token(
    settings: AppSettings,
    installation_id: int,
    *,
    http_client: httpx.Client | None = None,
) -> InstallationAccessToken:
    """Exchange a minted JWT for an installation-scoped access token.

    Args:
        settings: Application settings with GitHub App credentials.
        installation_id: Numeric ``installation.id`` from GitHub webhooks or
            the installations API.
        http_client: Optional shared :class:`httpx.Client`; when omitted a
            short-lived client is created for this request.

    Returns:
        Token and expiry parsed from GitHub's JSON response.

    Raises:
        GitHubAppAuthError: On transport errors, HTTP errors, or malformed JSON.
        ValueError: If ``installation_id`` is not positive.
    """

    if installation_id < 1:
        msg = "installation_id must be a positive integer"
        raise ValueError(msg)

    app_jwt = mint_github_app_jwt(settings)
    url = f"{_GITHUB_API_ORIGIN}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": _GITHUB_ACCEPT_HEADER,
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        response = client.post(url, headers=headers, json={})
    except httpx.HTTPError as exc:
        msg = "HTTP error while requesting installation access token"
        raise GitHubAppAuthError(msg) from exc
    finally:
        if owns_client:
            client.close()

    if response.status_code != httpx.codes.CREATED:
        msg = (
            "GitHub installation token request failed "
            f"(HTTP {response.status_code})"
        )
        raise GitHubAppAuthError(msg)

    try:
        body = response.json()
        raw_token = body["token"]
        raw_expires = body["expires_at"]
    except (KeyError, ValueError, TypeError) as exc:
        msg = "unexpected GitHub installation token response shape"
        raise GitHubAppAuthError(msg) from exc

    try:
        expires_at = datetime.fromisoformat(raw_expires.replace("Z", "+00:00"))
    except ValueError as exc:
        msg = "could not parse installation token expires_at"
        raise GitHubAppAuthError(msg) from exc

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    return InstallationAccessToken(
        token=SecretStr(raw_token),
        expires_at=expires_at,
    )
