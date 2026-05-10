"""Glob-pattern matching for `reviewgate-core` file categorization (\u00a710.6\u2013\u00a710.9).

Wraps :class:`pathspec.PathSpec` (gitignore semantics) behind a tiny
typed surface so the rest of the engine can ask the simple question
*"does this filename match any of these globs?"* without juggling pattern
objects. The :class:`PathMatcher` value compiles the patterns once at
construction time and exposes both single-file and "match by category"
helpers.

Why ``pathspec`` and not stdlib :mod:`fnmatch`:

* :mod:`fnmatch` does not handle ``**`` (any-depth path segment), which
  appears in every \u00a710.6\u2013\u00a710.9 list.
* :class:`pathlib.PurePath.match` only got proper ``**`` semantics in
  Python 3.13; this project supports 3.12.
* Gitignore semantics are what users already expect for a config like
  ``.reviewgate.yml`` (a basename pattern such as ``Dockerfile`` matches
  the file at any depth, ``vendor/**`` is anchored to the repo root).

The module performs no I/O. Pattern objects are immutable; a single
:class:`PathMatcher` may be shared across requests.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

import pathspec

_GITIGNORE: Final[str] = "gitignore"
"""Identifier for :mod:`pathspec`'s gitignore-style pattern factory.

Picked over the older ``gitwildmatch`` identifier (deprecated in
``pathspec`` 0.12) so the module does not emit ``DeprecationWarning`` on
every analysis run. Semantics are identical for the \u00a710.6\u2013\u00a710.9
patterns this engine consumes.
"""


class PathMatcher:
    """Pre-compiled gitignore-style matcher for a single pattern list.

    Construct one matcher per category (risky, dependency, lockfile,
    generated, vendored, minified, snapshot, test) and reuse it for the
    duration of an analysis run.

    Args:
        patterns: Iterable of glob strings (\u00a710.6\u2013\u00a710.9 syntax).
            An empty iterable produces a matcher that returns ``False`` for
            every input \u2014 useful when a category is disabled by config.
    """

    __slots__ = ("_patterns", "_spec")

    def __init__(self, patterns: Iterable[str]) -> None:
        # Materialize once so :meth:`patterns` can return a stable view
        # even when the caller passed a generator.
        self._patterns: tuple[str, ...] = tuple(patterns)
        self._spec: pathspec.PathSpec = pathspec.PathSpec.from_lines(
            _GITIGNORE,
            self._patterns,
        )

    @property
    def patterns(self) -> tuple[str, ...]:
        """The compiled pattern list, in declaration order."""

        return self._patterns

    def matches(self, filename: str) -> bool:
        """Return ``True`` iff ``filename`` matches any compiled pattern.

        ``filename`` is the repository-relative path emitted by GitHub
        (forward slashes, no leading slash). The matcher does not
        normalize paths; callers must pass them in the canonical form.
        """

        return self._spec.match_file(filename)

    def filter(self, filenames: Iterable[str]) -> list[str]:
        """Return the subset of ``filenames`` that match any pattern.

        Order is preserved. Useful for collecting "all generated files"
        or "all test files" from a changed-file list in one pass.
        """

        return [name for name in filenames if self._spec.match_file(name)]


def match_any(filename: str, patterns: Iterable[str]) -> bool:
    """One-shot convenience wrapper around :class:`PathMatcher`.

    Compiles ``patterns`` and returns whether ``filename`` matches any of
    them. Prefer constructing a long-lived :class:`PathMatcher` when
    matching the same list against many files; the convenience function
    is intended for one-off checks (tests, ad-hoc tooling).
    """

    return PathMatcher(patterns).matches(filename)


__all__ = ["PathMatcher", "match_any"]
