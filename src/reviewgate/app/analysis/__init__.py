"""Analysis pipeline package for the hosted GitHub App (``docs/DESIGN.md`` §15).

Caches, locks, and worker orchestration build on deterministic inputs from
``reviewgate.core`` while owning all network and Redis side effects here.
"""

from __future__ import annotations

# Keep package import lightweight. Console entry points such as
# ``reviewgate-worker`` are installed with the base distribution and need to
# import this package before optional hosted-App dependencies are available.
# Import concrete helpers from their modules instead of re-exporting them here.

__all__: list[str] = []
