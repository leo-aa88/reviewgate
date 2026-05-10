"""Tests for :mod:`reviewgate.app.github.auth`."""

from __future__ import annotations

import time
from typing import Final

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import SecretStr

pytest.importorskip("httpx")

from reviewgate.app.github.auth import (
    GITHUB_JWT_EFFECTIVE_TTL_SECONDS,
    GitHubAppAuthError,
    fetch_installation_access_token,
    mint_github_app_jwt,
)
from reviewgate.app.settings import AppSettings

_TEST_APP_ID: Final[int] = 99_887_766
_TEST_INSTALLATION_ID: Final[int] = 12_345


def _rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("ascii")


def _settings_with_github_app(pem: str) -> AppSettings:
    return AppSettings(
        github_app_id=_TEST_APP_ID,
        github_app_private_key=SecretStr(pem),
        github_webhook_secret=SecretStr("whsec_test"),
    )


def test_mint_github_app_jwt_payload_and_signature() -> None:
    """JWT carries ``iss`` (app id), ``iat``/``exp`` window, and verifies."""

    pem = _rsa_pem()
    settings = _settings_with_github_app(pem)
    fixed_now = time.time()
    token = mint_github_app_jwt(settings, now=fixed_now)
    private_key = load_pem_private_key(pem.encode("ascii"), password=None)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    decoded = jwt.decode(
        token,
        public_pem,
        algorithms=["RS256"],
        options={"require": ["exp", "iat", "iss"]},
        leeway=180,
    )
    assert decoded["iss"] == str(_TEST_APP_ID)
    assert decoded["iat"] == int(fixed_now) - 60
    assert decoded["exp"] == int(fixed_now) + GITHUB_JWT_EFFECTIVE_TTL_SECONDS


def test_mint_github_app_jwt_requires_credentials() -> None:
    """Missing app id or private key raises :class:`GitHubAppAuthError`."""

    with pytest.raises(GitHubAppAuthError, match="github_app_id"):
        mint_github_app_jwt(AppSettings(github_app_private_key=SecretStr(_rsa_pem())))
    with pytest.raises(GitHubAppAuthError, match="github_app_private_key"):
        mint_github_app_jwt(AppSettings(github_app_id=1))


def test_fetch_installation_access_token_parses_github_json() -> None:
    """HTTP 201 + JSON maps to :class:`~reviewgate.app.github.auth.InstallationAccessToken`."""

    pem = _rsa_pem()
    settings = _settings_with_github_app(pem)

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(_TEST_INSTALLATION_ID) in str(request.url)
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        return httpx.Response(
            httpx.codes.CREATED,
            json={
                "token": "ghs_example_installation_token",
                "expires_at": "2030-01-01T00:00:00Z",
            },
        )

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        result = fetch_installation_access_token(
            settings,
            _TEST_INSTALLATION_ID,
            http_client=client,
        )
    assert result.token.get_secret_value() == "ghs_example_installation_token"
    assert result.expires_at.year == 2030


def test_fetch_installation_access_token_http_error() -> None:
    """Non-201 responses surface as :class:`GitHubAppAuthError` without bodies."""

    pem = _rsa_pem()
    settings = _settings_with_github_app(pem)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(httpx.codes.FORBIDDEN, json={"message": "nope"})

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(GitHubAppAuthError, match="HTTP 403"):
            fetch_installation_access_token(
                settings,
                _TEST_INSTALLATION_ID,
                http_client=client,
            )


def test_app_settings_redacts_github_secrets() -> None:
    """``SecretStr`` fields must not appear verbatim in model repr."""

    pem = _rsa_pem()
    settings = _settings_with_github_app(pem)
    text = repr(settings)
    assert "BEGIN" not in text
    assert pem.strip()[:20] not in text
