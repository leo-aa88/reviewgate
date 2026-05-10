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
