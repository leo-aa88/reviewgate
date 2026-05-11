"""Hosted CLI entry points explain the optional ``app`` extra."""

from __future__ import annotations

import builtins
import importlib.machinery

import pytest


def test_reviewgate_api_missing_uvicorn_names_app_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A base install should not fail with a raw ``ModuleNotFoundError``."""

    from reviewgate.app.api_cli import main

    real_import = builtins.__import__

    def fake_import(
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "uvicorn":
            raise ModuleNotFoundError("No module named 'uvicorn'", name="uvicorn")
        return real_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert "reviewgate[app]" in str(exc_info.value)


def test_reviewgate_worker_missing_dramatiq_names_app_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker wrapper should fail before shelling out to ``python -m dramatiq``."""

    from reviewgate.app.analysis import worker_cli

    def fake_find_spec(name: str) -> importlib.machinery.ModuleSpec | None:
        if name == "dramatiq":
            return None
        return importlib.machinery.ModuleSpec(name, loader=None)

    monkeypatch.setattr(worker_cli.importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(SystemExit) as exc_info:
        worker_cli.main()

    assert "reviewgate[app]" in str(exc_info.value)
