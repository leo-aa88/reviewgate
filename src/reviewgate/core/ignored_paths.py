"""``.reviewgate.yml`` ``ignored_paths`` filtering (``docs/DESIGN.md`` §12).

Paths matching user-supplied globs are excluded from deterministic analysis:
they do not appear in ``file_categories``, do not contribute to size stats, and
do not participate in mixed-concern or coverage heuristics. GitHub still lists
them on the PR; teams use ``ignored_paths`` to silence lockfile-only noise or
generated trees without changing the upstream diff.
"""

from __future__ import annotations

from collections.abc import Iterable

from .paths import PathMatcher
from .schemas import ChangedFile


def filter_out_ignored_paths(
    files: Iterable[ChangedFile],
    patterns: Iterable[str],
) -> list[ChangedFile]:
    """Return ``files`` with any path matching ``patterns`` removed.

    Args:
        files: Changed files from the §10.1 payload (GitHub order preserved).
        patterns: Glob strings from ``ReviewGateConfig.ignored_paths``; empty
            or whitespace-only entries are skipped.

    Returns:
        A new list. When ``patterns`` is empty after stripping, this is
        ``list(files)`` with no allocations beyond the shallow list copy.
    """

    stripped = tuple(p.strip() for p in patterns if isinstance(p, str) and p.strip())
    if not stripped:
        return list(files)
    matcher = PathMatcher(stripped)
    return [f for f in files if not matcher.matches(f.filename)]


__all__ = ["filter_out_ignored_paths"]
