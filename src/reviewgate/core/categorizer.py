"""Per-file categorization for the deterministic engine (docs/DESIGN.md \u00a710.5).

Maps a :class:`reviewgate.core.schemas.ChangedFile` to the
:class:`reviewgate.core.schemas.FileCategoryRow` shape that the
:class:`ReviewabilityReport` exposes in its ``file_categories`` field.

Behaviour summary (\u00a710.5 + \u00a710.6 + \u00a710.4):

* Files may carry **multiple** \u00a710.5 categories (e.g. ``["source", "auth"]``
  for ``app/auth/session.ts``). Categories are accumulated by walking a
  fixed rule table; if no rule fires, the file is labelled ``"unknown"``.
* ``risky`` follows the user-configurable \u00a710.6 risky-path list (defaults
  to :data:`DEFAULT_RISKY_PATHS`). The label categories ``auth``,
  ``billing``, ``infra``, and ``migration`` are derived from spec-defined
  subsets of \u00a710.6 so they remain stable even if the user customises the
  ``risky_paths`` list.
* ``human_authored`` is ``False`` when a file is classified as
  ``generated``, ``lockfile``, ``snapshot``, ``vendored``, or ``minified``
  per the \u00a710.4 exclusion rule; \u00a710 (#10) sums these into total
  human-authored LOC.
* Pure: no I/O, no GitHub or LLM dependency. The categorizer can be
  called from the CLI, the GitHub Action, and the hosted App.

The matchers are pre-compiled once per :class:`Categorizer` instance so a
single instance can be reused across an analysis run without paying the
glob-compilation cost per file.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from .config import (
    DEFAULT_DEPENDENCY_FILES,
    DEFAULT_GENERATED_PATHS,
    DEFAULT_LOCKFILES,
    DEFAULT_MINIFIED_PATHS,
    DEFAULT_RISKY_PATHS,
    DEFAULT_SNAPSHOT_PATHS,
    DEFAULT_TEST_PATHS,
    DEFAULT_VENDORED_PATHS,
)
from .paths import PathMatcher
from .schemas import ChangedFile, FileCategory, FileCategoryRow

# --- spec-defined subsets of \u00a710.6 risky paths ---------------------------
#
# The \u00a710.5 category names ``migration``, ``auth``, ``billing``, and
# ``infra`` are derived from these stable subsets of the \u00a710.6 default
# list, so customising ``risky_paths`` cannot rename a category. The
# union of these four tuples equals :data:`DEFAULT_RISKY_PATHS`.

_MIGRATION_PATTERNS: Final[tuple[str, ...]] = (
    "**/migrations/**",
    "**/migration/**",
)
_AUTH_PATTERNS: Final[tuple[str, ...]] = (
    "**/auth/**",
    "**/authentication/**",
)
_BILLING_PATTERNS: Final[tuple[str, ...]] = (
    "**/billing/**",
    "**/payments/**",
)
_INFRA_PATTERNS: Final[tuple[str, ...]] = (
    "**/infra/**",
    "**/terraform/**",
    "**/.github/workflows/**",
    "Dockerfile",
    "docker-compose.yml",
    "compose.yml",
)

# --- extension- and basename-based categories -----------------------------
#
# Frozenset membership keeps lookups O(1) and the spelling explicit.

_DOCS_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".md", ".mdx", ".rst", ".adoc", ".txt"},
)
_DOCS_BASENAME_PREFIXES: Final[tuple[str, ...]] = (
    "README",
    "CHANGELOG",
    "LICENSE",
    "AUTHORS",
    "NOTICE",
    "CONTRIBUTING",
    "CODE_OF_CONDUCT",
)
_CONFIG_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties", ".env"},
)
_CONFIG_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        ".editorconfig",
        ".gitignore",
        ".gitattributes",
        ".prettierrc",
        ".eslintrc",
        ".dockerignore",
        ".npmrc",
        "Makefile",
    },
)
_ASSET_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mp3", ".mp4", ".wav", ".webm", ".mov", ".m4a",
        ".pdf",
    },
)
_SOURCE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        ".go", ".rs", ".java", ".kt", ".swift",
        ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh",
        ".cs", ".rb", ".php", ".scala", ".clj", ".cljs",
        ".ex", ".exs", ".elm", ".dart",
        ".lua", ".r", ".pl", ".pm",
        ".m", ".mm",
        ".sh", ".bash", ".zsh", ".ps1",
        ".html", ".htm", ".css", ".scss", ".sass", ".less",
        ".vue", ".svelte", ".sol",
        ".sql",
        ".graphql", ".proto",
    },
)

# --- \u00a710.4 exclusion rule ---------------------------------------------
#
# Any file carrying one of these categories is treated as not
# human-authored, so #10 will subtract its LOC from the total.

_NON_HUMAN_AUTHORED_CATEGORIES: Final[frozenset[FileCategory]] = frozenset(
    {"lockfile", "generated", "snapshot", "vendored", "minified"},
)


def _basename(filename: str) -> str:
    """Repository-relative basename without normalisation.

    The engine intentionally does not call :mod:`os.path` (which is
    platform-aware): GitHub always sends forward-slash paths, so a plain
    ``rsplit('/', 1)`` is correct and keeps the function pure.
    """

    return filename.rsplit("/", 1)[-1] if "/" in filename else filename


def _extension(filename: str) -> str:
    """Lowercased file extension including the leading dot.

    Returns the empty string for files with no extension (``Dockerfile``,
    ``Makefile``, etc.). Compound extensions like ``.tar.gz`` are
    deliberately reduced to the final component (``.gz``); the spec
    operates on simple suffixes.
    """

    base = _basename(filename)
    dot = base.rfind(".")
    if dot <= 0:
        # No dot, or a leading dot (dotfile such as ``.gitignore``) where
        # the leading dot is part of the name, not an extension.
        return ""
    return base[dot:].lower()


class Categorizer:
    """Stateless functional core wrapped in a class for matcher caching.

    Each instance compiles its glob patterns once at construction time.
    Construct one categorizer per analysis run and call
    :meth:`categorize` on each :class:`ChangedFile`.

    Args:
        risky_patterns: User-configurable \u00a710.6 risky-path list. Defaults
            to :data:`DEFAULT_RISKY_PATHS`. Customising this only changes
            ``risky``; the spec-defined category labels (``auth``,
            ``billing``, ``infra``, ``migration``) keep using the
            \u00a710.6 default subsets.
    """

    __slots__ = (
        "_auth",
        "_billing",
        "_dependency",
        "_generated",
        "_infra",
        "_lockfile",
        "_migration",
        "_minified",
        "_risky",
        "_snapshot",
        "_test",
        "_vendored",
    )

    def __init__(self, risky_patterns: Iterable[str] = DEFAULT_RISKY_PATHS) -> None:
        self._risky = PathMatcher(risky_patterns)
        self._test = PathMatcher(DEFAULT_TEST_PATHS)
        self._dependency = PathMatcher(DEFAULT_DEPENDENCY_FILES)
        self._lockfile = PathMatcher(DEFAULT_LOCKFILES)
        self._generated = PathMatcher(DEFAULT_GENERATED_PATHS)
        self._vendored = PathMatcher(DEFAULT_VENDORED_PATHS)
        self._minified = PathMatcher(DEFAULT_MINIFIED_PATHS)
        self._snapshot = PathMatcher(DEFAULT_SNAPSHOT_PATHS)
        self._migration = PathMatcher(_MIGRATION_PATTERNS)
        self._infra = PathMatcher(_INFRA_PATTERNS)
        self._auth = PathMatcher(_AUTH_PATTERNS)
        self._billing = PathMatcher(_BILLING_PATTERNS)

    def categorize(self, file: ChangedFile) -> FileCategoryRow:
        """Categorize a single :class:`ChangedFile` per \u00a710.5.

        Returns a :class:`FileCategoryRow` with one or more categories
        (``"unknown"`` if no rule matched), the \u00a710.6 ``risky`` boolean
        (computed against this instance's ``risky_patterns``), and the
        \u00a710.4 ``human_authored`` boolean.
        """

        filename = file.filename
        categories = self._collect_categories(filename)
        if not categories:
            categories = ["unknown"]

        risky = self._risky.matches(filename)
        human_authored = not any(
            cat in _NON_HUMAN_AUTHORED_CATEGORIES for cat in categories
        )
        return FileCategoryRow(
            filename=filename,
            categories=categories,
            risky=risky,
            human_authored=human_authored,
            changes=file.changes,
        )

    def categorize_all(self, files: Iterable[ChangedFile]) -> list[FileCategoryRow]:
        """Categorize every file, preserving input order."""

        return [self.categorize(f) for f in files]

    # --- internal -----------------------------------------------------

    def _collect_categories(self, filename: str) -> list[FileCategory]:
        """Walk the rule table once and accumulate matching \u00a710.5 labels.

        Order does not affect the spec semantics (each predicate is
        independent), but the table is laid out so categories that
        commonly cluster (test, dependency, lockfile, generated, ...)
        are grouped first, with extension-derived labels appended last.
        """

        categories: list[FileCategory] = []

        # Pattern-based categories
        if self._test.matches(filename):
            categories.append("test")
        if self._lockfile.matches(filename):
            categories.append("lockfile")
        if self._dependency.matches(filename):
            categories.append("dependency")
        if self._generated.matches(filename):
            categories.append("generated")
        if self._vendored.matches(filename):
            categories.append("vendored")
        if self._minified.matches(filename):
            categories.append("minified")
        if self._snapshot.matches(filename):
            categories.append("snapshot")
        if self._migration.matches(filename):
            categories.append("migration")
        if self._infra.matches(filename):
            categories.append("infra")
        if self._auth.matches(filename):
            categories.append("auth")
        if self._billing.matches(filename):
            categories.append("billing")

        # Extension- and basename-derived categories
        ext = _extension(filename)
        base = _basename(filename)

        is_docs = ext in _DOCS_EXTENSIONS or any(
            base.startswith(prefix) for prefix in _DOCS_BASENAME_PREFIXES
        )
        if is_docs:
            categories.append("docs")

        is_config = ext in _CONFIG_EXTENSIONS or base in _CONFIG_BASENAMES
        if is_config and "dependency" not in categories and "lockfile" not in categories:
            # Avoid double-counting; dependency manifests are also config
            # but the more specific label wins per the \u00a710.5 spirit.
            categories.append("config")

        if ext in _ASSET_EXTENSIONS:
            categories.append("asset")

        if ext in _SOURCE_EXTENSIONS:
            categories.append("source")

        return categories


def categorize_changed_files(
    files: Iterable[ChangedFile],
    *,
    risky_patterns: Iterable[str] = DEFAULT_RISKY_PATHS,
) -> list[FileCategoryRow]:
    """One-shot helper: build a :class:`Categorizer` and categorize ``files``.

    Convenience wrapper for callers (e.g. tests, the engine orchestrator)
    that only need a single pass. Long-lived consumers should construct
    a :class:`Categorizer` directly so the matcher set is reused.
    """

    return Categorizer(risky_patterns=risky_patterns).categorize_all(files)


__all__ = ["Categorizer", "categorize_changed_files"]
