"""FastAPI ASGI application for the hosted ReviewGate GitHub App (§15 / §17.2).

This module defines the production ``app`` object consumed by ASGI servers
such as uvicorn. Webhook routes and dependency wiring land in later issues
(#33 onward); only the §17.2 health probe is exposed here.

Example:
    In-process ASGI tests::

        from fastapi.testclient import TestClient
        from reviewgate.app.main import create_app

        client = TestClient(create_app())
        assert client.get("/health").json() == {"ok": True}
"""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Construct a fresh :class:`fastapi.FastAPI` instance."""

    application = FastAPI(
        title="ReviewGate",
        version="0.1.0",
        summary="Hosted GitHub App HTTP surface (skeleton; issue #32).",
    )

    @application.get("/health")
    def health() -> dict[str, bool]:
        """Return the §17.2 liveness payload."""

        return {"ok": True}

    return application


app = create_app()
