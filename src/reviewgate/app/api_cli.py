"""Console entry for ``reviewgate-api`` (uvicorn wrapper, issue #32).

Reads :class:`~reviewgate.app.settings.AppSettings` for the bind port and
starts uvicorn against :data:`reviewgate.app.main.app`.
"""

from __future__ import annotations


def main() -> None:
    """Run uvicorn with the packaged FastAPI application."""

    import uvicorn

    from reviewgate.app.settings import AppSettings

    settings = AppSettings()
    uvicorn.run(
        "reviewgate.app.main:app",
        host="0.0.0.0",
        port=settings.http_port,
    )
