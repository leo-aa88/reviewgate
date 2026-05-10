"""Smoke tests for distribution layout (PEP 517 / editable installs).

CI runs ``pip install -e ".[dev]"`` before ``pytest``; ``pythonpath`` in
``pyproject.toml`` must not prepend ``src/``, or imports would bypass the
installed layout. These tests fail fast when the Action runtime is not
importable as a real top-level package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import reviewgate


def test_reviewgate_packages_resolve_through_importlib() -> None:
    """``reviewgate`` and ``reviewgate_action`` must be standard discoverable packages."""
    for root in ("reviewgate", "reviewgate_action"):
        spec = importlib.util.find_spec(root)
        assert spec is not None, f"missing import spec for {root}"
        assert spec.origin is not None or spec.submodule_search_locations, (
            f"{root} must map to package locations on disk"
        )


def test_reviewgate_root_has_py_typed_marker() -> None:
    """PEP 561: top-level ``reviewgate`` must ship ``py.typed`` for downstream checkers."""

    root = Path(reviewgate.__file__).resolve().parent
    assert (root / "py.typed").is_file()


def test_reviewgate_action_package_dir_exists() -> None:
    """The runtime package must expose a concrete namespace path."""
    import reviewgate_action

    paths = list(reviewgate_action.__path__)
    assert paths, "reviewgate_action.__path__ must be non-empty"
    assert Path(paths[0]).is_dir()
