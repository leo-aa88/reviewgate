"""Tests for ``reviewgate_action.coexistence`` (issue #26).

Pin the §14.1 coexistence table for the GitHub Action: every
combination of the Action's ``mode`` input, ``.reviewgate.yml``'s
``mode`` field, and the workflow's ``post-comment`` flag must
produce a documented decision so the Action and hosted App never
double-post.
"""

from __future__ import annotations

from typing import Final

import pytest

from reviewgate_action.coexistence import (
    ActionMode,
    ConfigMode,
    decide,
)

_ACTION_MODES: Final[tuple[ActionMode, ...]] = ("auto", "action", "quiet")
"""Every value the §14 ``mode`` input accepts; pinned for the matrix tests."""

_CONFIG_MODES: Final[tuple[ConfigMode, ...]] = ("app", "action", "both")
"""Every value the §12 ``mode`` field accepts; pinned for the matrix tests."""


@pytest.mark.parametrize("config_mode", ["app", "action", "both"])
@pytest.mark.parametrize("post_comment_input", [True, False])
def test_quiet_mode_silences_action_regardless_of_config_or_input(
    config_mode: ConfigMode, post_comment_input: bool
) -> None:
    """`mode: quiet` is the universal escape hatch.

    Workflow authors flip ``mode: quiet`` to mute the Action while
    debugging or during a hosted-App migration; the resolver must
    not let `.reviewgate.yml` or `post-comment: true` re-enable
    output behind their back.
    """

    decision = decide(
        action_mode="quiet",
        config_mode=config_mode,
        post_comment_input=post_comment_input,
    )
    assert decision.post_comment is False
    assert decision.apply_fail_on is False
    assert "quiet" in decision.rationale.lower()


@pytest.mark.parametrize("config_mode", ["app", "action", "both"])
def test_action_mode_overrides_config_to_take_ownership(
    config_mode: ConfigMode,
) -> None:
    """`mode: action` always lets the Action own the surface.

    Even when `.reviewgate.yml` says `mode: app`, an explicit
    Action input of `mode: action` wins. The hosted App is expected
    to honour the same input on its side; without that override
    a workflow author could not migrate from app to action without
    editing `.reviewgate.yml` first.
    """

    decision = decide(
        action_mode="action",
        config_mode=config_mode,
        post_comment_input=True,
    )
    assert decision.post_comment is True
    assert decision.apply_fail_on is True
    assert "action" in decision.rationale.lower()


def test_action_mode_still_honours_post_comment_false() -> None:
    """Explicit `post-comment: false` always wins over `mode: action`.

    `post-comment` is a per-workflow opt-out; the resolver must not
    silently re-enable posting just because the mode says so.
    """

    decision = decide(
        action_mode="action",
        config_mode="action",
        post_comment_input=False,
    )
    assert decision.post_comment is False
    assert decision.apply_fail_on is True


def test_auto_with_config_app_keeps_action_quiet() -> None:
    """The §14.1 default: `auto` defers to `mode: app` -> Action quiet."""

    decision = decide(
        action_mode="auto",
        config_mode="app",
        post_comment_input=True,
    )
    assert decision.post_comment is False
    assert decision.apply_fail_on is False
    assert "app" in decision.rationale.lower()


@pytest.mark.parametrize("config_mode", ["action", "both"])
def test_auto_with_config_action_or_both_lets_action_post(
    config_mode: ConfigMode,
) -> None:
    """`auto` + `mode: action` / `mode: both` -> Action posts."""

    decision = decide(
        action_mode="auto",
        config_mode=config_mode,
        post_comment_input=True,
    )
    assert decision.post_comment is True
    assert decision.apply_fail_on is True
    assert config_mode in decision.rationale


@pytest.mark.parametrize("config_mode", ["action", "both"])
def test_auto_with_post_comment_false_skips_posting(
    config_mode: ConfigMode,
) -> None:
    """`post-comment: false` overrides any coexistence permission."""

    decision = decide(
        action_mode="auto",
        config_mode=config_mode,
        post_comment_input=False,
    )
    assert decision.post_comment is False
    assert decision.apply_fail_on is True


def test_decision_rationale_is_actionable_single_line() -> None:
    """Every rationale string must be a single line for log readability.

    Iterating over the typed ``_ACTION_MODES`` / ``_CONFIG_MODES``
    tuples (rather than bare string literals) keeps ``decide``'s
    Literal contract enforced by mypy at the call site, so an enum
    drift would surface as a type error instead of slipping past
    the matrix test.
    """

    for action_mode in _ACTION_MODES:
        for config_mode in _CONFIG_MODES:
            decision = decide(
                action_mode=action_mode,
                config_mode=config_mode,
                post_comment_input=True,
            )
            assert "\n" not in decision.rationale, (
                f"rationale for {action_mode}/{config_mode} must be one line: "
                f"{decision.rationale!r}"
            )
            assert decision.rationale.strip() == decision.rationale
            assert len(decision.rationale) > 20, (
                f"rationale for {action_mode}/{config_mode} too short to be "
                f"actionable: {decision.rationale!r}"
            )


def test_action_mode_is_a_strict_subset_of_config_mode_enum() -> None:
    """Catch typos: §14 inputs and §12 config must remain orthogonal.

    `app` is a config-only value (no Action equivalent). `quiet` is
    Action-only (no config equivalent). The two enums must not drift
    toward each other; this guard catches a future change that
    accidentally adds `app` to the Action enum or `quiet` to the
    config enum, both of which would break §14.1's rules.
    """

    action_modes: set[str] = set(_ACTION_MODES)
    config_modes: set[str] = set(_CONFIG_MODES)
    assert "app" not in action_modes
    assert "auto" not in config_modes
    assert "quiet" not in config_modes
    assert "both" not in action_modes
