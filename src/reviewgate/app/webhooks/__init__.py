"""GitHub webhook receivers for the hosted app (``docs/DESIGN.md`` §13.3)."""

from __future__ import annotations

from reviewgate.app.webhooks.github import router as github_router

__all__ = ["github_router"]
