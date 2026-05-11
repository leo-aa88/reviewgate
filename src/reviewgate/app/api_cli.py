"""Console entry for ``reviewgate-api`` (uvicorn wrapper, issue #32).

Reads :class:`~reviewgate.app.settings.AppSettings` for the bind port and
starts uvicorn against :data:`reviewgate.app.main.app`.
"""

from __future__ import annotations

_APP_EXTRA_MESSAGE = (
    "reviewgate-api requires the hosted app dependencies. "
    'Install them with: pip install "reviewgate[app]"'
)


def main() -> None:
    """Run uvicorn with the packaged FastAPI application."""

    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        if exc.name == "uvicorn":
            raise SystemExit(_APP_EXTRA_MESSAGE) from exc
        raise

    try:
        from reviewgate.app.settings import AppSettings
    except ModuleNotFoundError as exc:
        if exc.name in {"pydantic_settings", "sqlalchemy", "fastapi"}:
            raise SystemExit(_APP_EXTRA_MESSAGE) from exc
        raise

    settings = AppSettings()
    uvicorn.run(
        "reviewgate.app.main:app",
        host="0.0.0.0",
        port=settings.http_port,
    )
