"""Tests for Dramatiq actors (``reviewgate.app.analysis.jobs``)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

pytest.importorskip("dramatiq")

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent


def test_run_pr_analysis_stub_invokes_with_stub_broker() -> None:
    """Actors register against a :class:`~dramatiq.brokers.stub.StubBroker`."""

    import dramatiq
    from dramatiq.brokers.stub import StubBroker

    dramatiq.set_broker(StubBroker())
    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub({"pull_number": 1})


def test_worker_app_import_requires_redis_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``worker_app`` refuses to import without ``REVIEWGATE_REDIS_URL``."""

    monkeypatch.delenv("REVIEWGATE_REDIS_URL", raising=False)
    code = (
        "import importlib, sys\n"
        "try:\n"
        "    importlib.import_module('reviewgate.app.analysis.worker_app')\n"
        "except RuntimeError as exc:\n"
        "    sys.exit(0 if 'REVIEWGATE_REDIS_URL' in str(exc) else 2)\n"
        "sys.exit(1)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={
            **dict(os.environ),
            "REVIEWGATE_DISABLE_WEBHOOK_PURGE_SCHEDULER": "1",
        },
    )
    assert proc.returncode == 0, proc.stderr
