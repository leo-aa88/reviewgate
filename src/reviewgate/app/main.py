"""FastAPI ASGI application for the hosted ReviewGate GitHub App (§15 / §17.2).

This module defines the production ``app`` object consumed by ASGI servers
such as uvicorn. It wires the §17.2 health probe, the §17.1 GitHub webhook
route (issue #33), and optional Dramatiq broker startup when Redis is
configured.

Example:
    In-process ASGI tests::

        from fastapi.testclient import TestClient
        from reviewgate.app.main import create_app

        client = TestClient(create_app())
        assert client.get("/health").json() == {"ok": True}
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from reviewgate.app.settings import AppSettings
from reviewgate.app.webhooks import github_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Install Dramatiq broker during API startup when Redis is available."""

    settings = AppSettings()
    app.state.settings = settings
    if settings.redis_url is not None:
        from reviewgate.app.analysis.broker_install import install_redis_broker

        install_redis_broker(settings)
    yield


def create_app() -> FastAPI:
    """Construct a fresh :class:`fastapi.FastAPI` instance."""

    application = FastAPI(
        title="ReviewGate",
        version="0.1.0",
        summary="Hosted GitHub App HTTP surface.",
        lifespan=_lifespan,
    )

    application.include_router(github_router)

    @application.get("/health")
    def health() -> dict[str, bool]:
        """Return the §17.2 liveness payload."""

        return {"ok": True}

    return application


app = create_app()
