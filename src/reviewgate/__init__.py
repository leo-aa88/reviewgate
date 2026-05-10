"""ReviewGate open-source package (deterministic engine in :mod:`reviewgate.core`)."""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__: str = version("reviewgate")
except PackageNotFoundError:  # pragma: no cover - editable/tostring dev layouts
    __version__ = "0.0.0"

__all__ = ["__version__", "core"]


def __getattr__(name: str) -> Any:
    """Lazy ``reviewgate.core`` so importing :mod:`reviewgate` stays side-effect free."""
    if name == "core":
        return import_module("reviewgate.core")
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
