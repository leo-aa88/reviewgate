"""Typed application settings for the hosted ReviewGate app (``docs/DESIGN.md`` §15).

Environment variables use the ``REVIEWGATE_`` prefix so they do not collide with
unrelated processes on shared developer machines. Settings are optional where
the app can still boot without external services (for example Redis in early
local development).

Example:
    Loading Redis URL from the environment::

        import os

        os.environ["REVIEWGATE_REDIS_URL"] = "redis://127.0.0.1:6379/0"
        assert AppSettings().redis_url == "redis://127.0.0.1:6379/0"
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Process-wide configuration for ``reviewgate.app``."""

    model_config = SettingsConfigDict(
        env_prefix="REVIEWGATE_",
        extra="ignore",
    )

    redis_url: str | None = Field(
        default=None,
        description=(
            "Redis connection URL for analysis caches, worker locks, and "
            "internal rate limits (``docs/DESIGN.md`` §13.6–§13.7, §22.2). "
            "When unset, Redis-backed features stay disabled until staging "
            "provides a URL."
        ),
    )
    database_url: str | None = Field(
        default=None,
        description=(
            "PostgreSQL URL for SQLAlchemy (``docs/DESIGN.md`` §16). Uses the "
            "same ``REVIEWGATE_DATABASE_URL`` name as Alembic migrations so "
            "operators configure one DSN for schema upgrades and runtime."
        ),
    )
    http_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="TCP port for the hosted HTTP server (``GET /health``, issue #32).",
    )
    github_app_id: int | None = Field(
        default=None,
        description=(
            "Numeric GitHub App ID (``docs/DESIGN.md`` §13.4) used as the JWT "
            "``iss`` claim."
        ),
    )
    github_app_private_key: SecretStr | None = Field(
        default=None,
        description=(
            "PEM-encoded RSA private key for signing GitHub App JWTs. Never "
            "log this value; use ``.get_secret_value()`` only at signing time."
        ),
    )
    github_webhook_secret: SecretStr | None = Field(
        default=None,
        description=(
            "Secret for ``X-Hub-Signature-256`` webhook verification "
            "(``docs/DESIGN.md`` §13.3). Never log this value."
        ),
    )
    legacy_installation_deleted_webhook_204: bool = Field(
        default=False,
        description=(
            "When true, ``installation.deleted`` returns **204** and skips "
            "persistence (pre-issue-#36 behavior) for emergency rollback."
        ),
    )
    github_app_install_url: str | None = Field(
        default=None,
        description=(
            "Public HTTPS URL for the GitHub App “Install” flow, shown on the "
            "§5.1 landing page (issue #38)."
        ),
    )
    github_app_bot_login: str | None = Field(
        default=None,
        description=(
            "Exact GitHub ``user.login`` for the App installation bot "
            "(for example ``my-app[bot]``). Used when upserting PR comments "
            "(``docs/DESIGN.md`` §13.8; issue #51). Overrides "
            "``github_app_slug``-derived login when set."
        ),
    )
    github_app_slug: str | None = Field(
        default=None,
        description=(
            "GitHub App ``slug`` from the App settings URL. When "
            "``github_app_bot_login`` is unset, comment upsert resolves the bot "
            "login as ``{slug}[bot]`` (§13.8)."
        ),
    )
