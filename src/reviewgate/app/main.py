"""FastAPI ASGI application for the hosted ReviewGate GitHub App (§15 / §17.2).

This module defines the production ``app`` object consumed by ASGI servers
such as uvicorn. It wires the §17.2 health probe, the §17.1 GitHub webhook
route (issue #33), the §5.1 ``GET /privacy`` page (issue #37), the minimal
landing, install-success, and feedback pages (issues #38, #55), and
``POST /api/beta-leads`` / ``POST /api/beta-feedback`` (issues #39, #55).
Optional hosted LLM enrichment runs in the worker when ``llm_reports`` is
enabled (§11; issues #57–#64). The webhook installs the Dramatiq Redis broker on demand
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

from reviewgate.app.beta_feedback import router as beta_feedback_router
from reviewgate.app.beta_leads import router as beta_leads_router
from reviewgate.app.privacy import router as privacy_router
from reviewgate.app.site_pages import router as site_pages_router
from reviewgate.app.webhooks import github_router


def create_app() -> FastAPI:
    """Construct a fresh :class:`fastapi.FastAPI` instance."""

    application = FastAPI(
        title="ReviewGate",
        version="0.1.0",
        summary="Hosted GitHub App HTTP surface.",
    )

    application.include_router(github_router)
    application.include_router(beta_leads_router)
    application.include_router(beta_feedback_router)
    application.include_router(privacy_router)
    application.include_router(site_pages_router)

    @application.get("/health")
    def health() -> dict[str, bool]:
        """Return the §17.2 liveness payload."""

        return {"ok": True}

    return application


app = create_app()
