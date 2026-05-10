"""Hosted GitHub App package surface (:mod:`reviewgate.app`).

The deterministic engine lives in :mod:`reviewgate.core` and stays free of
database and network I/O (``docs/DESIGN.md`` §4.1). Persistence, webhooks,
and GitHub integration ship under ``reviewgate.app`` per §15.
"""

from __future__ import annotations

__all__: list[str] = []
