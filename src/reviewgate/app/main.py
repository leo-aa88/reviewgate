"""FastAPI ASGI application for the hosted ReviewGate GitHub App (§15 / §17.2).

This module defines the production ``app`` object consumed by ASGI servers
such as uvicorn. It wires the §17.2 health probe and the §17.1 GitHub webhook
route (issue #33). The webhook installs the Dramatiq Redis broker on demand
when enqueueing (see :mod:`reviewgate.app.analysis.broker_install`).

Example:
    In-process ASGI tests::

        from fastapi.testclient import TestClient
        from reviewgate.app.main import create_app

        client = TestClient(create_app())
        assert client.get("/health").json() == {"ok": True}
"""

from __future__ import annotations

from fastapi import FastAPI

from reviewgate.app.webhooks import github_router


def create_app() -> FastAPI:
    """Construct a fresh :class:`fastapi.FastAPI` instance."""

    application = FastAPI(
        title="ReviewGate",
        version="0.1.0",
        summary="Hosted GitHub App HTTP surface.",
    )

    application.include_router(github_router)

    @application.get("/health")
    def health() -> dict[str, bool]:
        """Return the §17.2 liveness payload."""

        return {"ok": True}

    return application


app = create_app()
