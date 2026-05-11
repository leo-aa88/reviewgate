"""GitHub App integration for the hosted ReviewGate service (``docs/DESIGN.md`` §13).

This package owns server-to-server authentication, REST clients, and PR
comment upsert, label sync, and Checks API helpers (§13.8–§13.10). The deterministic engine in
:mod:`reviewgate.core` must never import from here.
"""

from __future__ import annotations

# Keep package import lightweight. The hosted App dependencies are optional, so
# importing ``reviewgate.app.github`` must not eagerly import HTTP, JWT, or
# settings modules. Import concrete helpers from their modules instead.

__all__: list[str] = []
