"""Tests for ``schedule_daily_webhook_purge`` (issue #34)."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


def test_schedule_daily_webhook_purge_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No thread is started when ``REVIEWGATE_DISABLE_WEBHOOK_PURGE_SCHEDULER=1``."""

    monkeypatch.setenv("REVIEWGATE_DISABLE_WEBHOOK_PURGE_SCHEDULER", "1")
    with patch("reviewgate.app.analysis.webhook_purge_scheduler.threading.Thread") as thread_cls:
        import reviewgate.app.analysis.webhook_purge_scheduler as scheduler

        importlib.reload(scheduler)
        scheduler.schedule_daily_webhook_purge()
    thread_cls.assert_not_called()


def test_schedule_daily_webhook_purge_starts_one_daemon_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enabled, exactly one daemon background thread is registered."""

    monkeypatch.delenv("REVIEWGATE_DISABLE_WEBHOOK_PURGE_SCHEDULER", raising=False)
    with patch("reviewgate.app.analysis.webhook_purge_scheduler.threading.Thread") as thread_cls:
        import reviewgate.app.analysis.webhook_purge_scheduler as scheduler

        importlib.reload(scheduler)
        scheduler.schedule_daily_webhook_purge()
    thread_cls.assert_called_once()
    assert thread_cls.call_args.kwargs.get("daemon") is True
