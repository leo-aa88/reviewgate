"""Tests for :mod:`reviewgate.app.github.coexistence` (issue #54)."""

from __future__ import annotations

from reviewgate.app.github.coexistence import (
    effective_hosted_status_check,
    hosted_github_outputs_enabled,
)
from reviewgate.core.config import (
    DEFAULT_STATUS_CHECK_NAME,
    ReviewGateConfig,
    StatusCheck,
)


def test_hosted_outputs_disabled_for_action_mode() -> None:
    assert hosted_github_outputs_enabled(ReviewGateConfig(mode="action")) is False


def test_hosted_outputs_enabled_for_app_and_both() -> None:
    assert hosted_github_outputs_enabled(ReviewGateConfig(mode="app")) is True
    assert hosted_github_outputs_enabled(ReviewGateConfig(mode="both")) is True


def test_effective_status_check_unchanged_for_app() -> None:
    cfg = ReviewGateConfig(mode="app")
    assert effective_hosted_status_check(cfg) == cfg.status_check


def test_effective_status_check_suffix_for_both_with_default_name() -> None:
    cfg = ReviewGateConfig(mode="both")
    sc = effective_hosted_status_check(cfg)
    assert sc.name == f"{DEFAULT_STATUS_CHECK_NAME} (hosted)"


def test_effective_status_check_custom_name_unchanged_in_both() -> None:
    cfg = ReviewGateConfig(
        mode="both",
        status_check=StatusCheck(name="custom/reviewgate", enabled=True),
    )
    assert effective_hosted_status_check(cfg).name == "custom/reviewgate"
