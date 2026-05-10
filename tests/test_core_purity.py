"""Static purity guard for ``reviewgate.core`` (docs/DESIGN.md \u00a74.1).

\u00a74.1 codifies the engine boundary in load-bearing terms: the deterministic
core *must* run as ``(pr_metadata, changed_files, config) -> report`` with
**no GitHub API calls, no network I/O, no filesystem writes, no database
access, no LLM calls, no comments/labels/status checks, no side effects**.

Detecting arbitrary side effects at runtime is intractable. What is
tractable, and what catches every realistic regression a contributor
would introduce, is checking that ``reviewgate.core`` never *imports* a
third-party I/O library or a stdlib network/process module. This module
parses every ``.py`` file under ``src/reviewgate/core/`` with
:mod:`ast` (no execution, no side effects of its own) and asserts the
import set stays inside an allow-listed contract.

If you are here because this test failed: see ``CONTRIBUTING.md`` \u00b6
"reviewgate-core purity boundary" before disabling. The right fix is
almost always to move the offending dependency to the GitHub Action or
hosted App layer (\u00a74.2 / \u00a74.3), not to expand the allow-list.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

# --- forbidden-import contract ----------------------------------------------

# Top-level packages that are *categorically* not allowed inside
# ``reviewgate.core``. Each entry is grouped by the rule of \u00a74.1 it
# protects so that a future addition has an obvious home.

_FORBIDDEN_THIRD_PARTY_HTTP: Final[frozenset[str]] = frozenset(
    {"httpx", "requests", "aiohttp", "urllib3", "h11", "h2"},
)
_FORBIDDEN_THIRD_PARTY_DB: Final[frozenset[str]] = frozenset(
    {
        "sqlalchemy",
        "psycopg",
        "psycopg2",
        "psycopg2cffi",
        "asyncpg",
        "pymongo",
        "motor",
        "redis",
        "aioredis",
        "valkey",
    },
)
_FORBIDDEN_THIRD_PARTY_LLM: Final[frozenset[str]] = frozenset(
    {"openai", "anthropic", "cohere", "mistralai", "litellm"},
)
_FORBIDDEN_THIRD_PARTY_CLOUD_AND_GITHUB: Final[frozenset[str]] = frozenset(
    {
        "boto3",
        "botocore",
        "github",
        "githubkit",
        "gidgethub",
    },
)

# Stdlib modules that are themselves I/O surfaces. ``urllib.parse`` is
# *not* listed because it is pure string manipulation; ``urllib.request``
# / ``urllib.error`` are.
_FORBIDDEN_STDLIB_NETWORK: Final[frozenset[str]] = frozenset(
    {
        "socket",
        "ssl",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "nntplib",
        "telnetlib",
        "http.client",
        "http.server",
        "urllib.request",
        "urllib.error",
        "xmlrpc.client",
        "xmlrpc.server",
    },
)
_FORBIDDEN_STDLIB_PROCESS: Final[frozenset[str]] = frozenset(
    {"subprocess", "asyncio.subprocess", "multiprocessing"},
)

# Dotted-prefix bans for namespaces (e.g. ``google.cloud.storage``).
_FORBIDDEN_PREFIXES: Final[tuple[str, ...]] = (
    "google.cloud.",
    "google.generativeai",
    "google.genai",
)

_FORBIDDEN_EXACT: Final[frozenset[str]] = (
    _FORBIDDEN_THIRD_PARTY_HTTP
    | _FORBIDDEN_THIRD_PARTY_DB
    | _FORBIDDEN_THIRD_PARTY_LLM
    | _FORBIDDEN_THIRD_PARTY_CLOUD_AND_GITHUB
    | _FORBIDDEN_STDLIB_NETWORK
    | _FORBIDDEN_STDLIB_PROCESS
)


# --- AST helpers ------------------------------------------------------------


_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_CORE_DIR: Final[Path] = _REPO_ROOT / "src" / "reviewgate" / "core"


def _module_is_forbidden(module: str) -> bool:
    """Return True iff ``module`` (or a parent of it) is in the contract.

    A dotted name is forbidden when its leftmost segment matches an exact
    ban (``redis`` blocks ``redis.asyncio``), when the name itself matches
    a multi-segment exact ban (``urllib.request``), or when it starts
    with one of the namespace prefixes (``google.cloud.storage``).
    """

    if module in _FORBIDDEN_EXACT:
        return True
    head = module.split(".", 1)[0]
    if head in _FORBIDDEN_EXACT:
        return True
    return any(module.startswith(prefix) for prefix in _FORBIDDEN_PREFIXES)


def _imports_in_file(path: Path) -> Iterator[str]:
    """Yield every imported module name (dotted) found in ``path``.

    ``ast.parse`` is pure: it neither executes the file nor resolves the
    import. Both ``import a.b`` and ``from a.b import c`` are surfaced
    as ``a.b``.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                continue
            yield node.module


def _core_python_files() -> list[Path]:
    """Return every ``.py`` file shipped inside ``src/reviewgate/core/``."""

    assert _CORE_DIR.is_dir(), f"reviewgate.core source dir missing: {_CORE_DIR}"
    return sorted(_CORE_DIR.rglob("*.py"))


# --- tests ------------------------------------------------------------------


def test_core_directory_is_not_empty() -> None:
    """Sanity: if this is empty the purity check is silently vacuous."""

    files = _core_python_files()
    assert files, f"no python files under {_CORE_DIR}; purity guard would be vacuous"


@pytest.mark.parametrize("py_file", _core_python_files(), ids=lambda p: p.name)
def test_core_module_imports_only_allowed_modules(py_file: Path) -> None:
    """Per-file guard: every ``reviewgate.core`` module obeys \u00a74.1.

    Parametrized per file so a regression names the offending module
    instead of dumping the whole core into one error.
    """

    offenders = sorted(
        {imp for imp in _imports_in_file(py_file) if _module_is_forbidden(imp)},
    )
    assert not offenders, (
        f"{py_file.relative_to(_REPO_ROOT)} imports "
        f"forbidden module(s) {offenders!r}; see CONTRIBUTING.md "
        f"\u00b6 'reviewgate-core purity boundary' (DESIGN.md \u00a74.1)."
    )


def test_runtime_dependencies_stay_pure() -> None:
    """Cross-check: no forbidden package leaks into runtime ``dependencies``.

    Parsing ``pyproject.toml`` with :mod:`tomllib` keeps this test
    stdlib-only and side-effect-free.
    """

    import tomllib

    pyproject_path = _REPO_ROOT / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    deps = pyproject.get("project", {}).get("dependencies", [])
    offenders: list[str] = []
    for raw in deps:
        name = (
            raw.split(";", 1)[0]
            .split("[", 1)[0]
            .split(">=", 1)[0]
            .split("==", 1)[0]
            .split("<", 1)[0]
            .split(">", 1)[0]
            .strip()
            .lower()
        )
        if name in {f.lower() for f in _FORBIDDEN_EXACT}:
            offenders.append(raw)
    assert not offenders, (
        f"pyproject.toml runtime dependencies pull in forbidden packages: {offenders!r}"
    )


def test_purity_guard_detects_a_planted_violation() -> None:
    """Self-test: the AST walker actually flags a known-bad import.

    Without this, a future refactor could quietly disable the guard
    (e.g. by typo) and every per-file test would still pass vacuously.
    """

    src = "import requests\nimport reviewgate.core.schemas\n"
    tree = ast.parse(src)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    flagged = [m for m in found if _module_is_forbidden(m)]
    assert flagged == ["requests"], (
        "purity guard failed to flag a planted `import requests`; the "
        "AST walk or forbidden table is broken."
    )
