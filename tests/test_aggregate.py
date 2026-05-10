"""Tests for :func:`reviewgate.core.aggregate.baseline_reviewability` (\u00a710.13).

These tests lock the deterministic decision ladder documented in
``docs/DESIGN.md`` \u00a710.13. Every branch of the ladder is covered with a
parametrized boundary case so a regression on any single rule produces a
targeted failure message.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from reviewgate.core import baseline_reviewability
from reviewgate.core.schemas import (
    EngineWarning,
    Reviewability,
    WarningSeverity,
)


def _w(severity: WarningSeverity, code: str = "test/warn") -> EngineWarning:
    """Build a minimal valid :class:`EngineWarning` with the given severity.

    Only ``severity`` matters for \u00a710.13; ``code``/``message`` are required by
    the schema but irrelevant to aggregation. A factory keeps test bodies
    focused on the boundary being asserted.
    """

    return EngineWarning(code=code, severity=severity, message="x")


@pytest.mark.parametrize(
    ("high", "medium", "low", "expected"),
    [
        pytest.param(0, 0, 0, "PASS", id="empty"),
        pytest.param(0, 0, 5, "PASS", id="only-low-never-affects-baseline"),
        pytest.param(0, 1, 0, "PASS", id="one-medium-stays-pass"),
        pytest.param(0, 1, 9, "PASS", id="one-medium-plus-low-stays-pass"),
        pytest.param(0, 2, 0, "WARN", id="two-medium-warn-boundary"),
        pytest.param(0, 7, 0, "WARN", id="many-medium-still-warn"),
        pytest.param(1, 0, 0, "WARN", id="one-high-no-medium-warn"),
        pytest.param(1, 0, 9, "WARN", id="one-high-plus-low-still-warn"),
        pytest.param(1, 1, 0, "FAIL", id="one-high-plus-one-medium-fail-boundary"),
        pytest.param(1, 5, 0, "FAIL", id="one-high-plus-many-medium-fail"),
        pytest.param(2, 0, 0, "FAIL", id="two-high-fail-boundary"),
        pytest.param(2, 0, 9, "FAIL", id="two-high-plus-low-fail"),
        pytest.param(5, 5, 5, "FAIL", id="many-of-everything-fail"),
    ],
)
def test_baseline_reviewability_matches_design_ladder(
    high: int,
    medium: int,
    low: int,
    expected: Reviewability,
) -> None:
    """Every branch of the \u00a710.13 ladder yields the documented verdict."""

    warnings = (
        [_w("high")] * high + [_w("medium")] * medium + [_w("low")] * low
    )
    assert baseline_reviewability(warnings) == expected


def test_baseline_reviewability_is_order_independent() -> None:
    """Shuffling the warning list cannot change the verdict (\u00a710.13)."""

    forward = [_w("high"), _w("medium"), _w("low")]
    reverse = list(reversed(forward))
    assert baseline_reviewability(forward) == baseline_reviewability(reverse)


def test_baseline_reviewability_does_not_mutate_input() -> None:
    """Aggregation must not consume or alter the caller's list."""

    warnings = [_w("high"), _w("medium")]
    snapshot = list(warnings)
    baseline_reviewability(warnings)
    assert warnings == snapshot


def test_baseline_reviewability_accepts_generator_inputs() -> None:
    """The signature uses ``Iterable[EngineWarning]`` and accepts generators.

    This guards against future refactors that would silently require a
    list (``len(...)``) or repeat-iterate the input (which a generator
    cannot satisfy).
    """

    def gen() -> Iterator[EngineWarning]:
        yield _w("high")
        yield _w("medium")

    assert baseline_reviewability(gen()) == "FAIL"


def test_baseline_reviewability_idempotent_on_same_input() -> None:
    """Calling the function twice on the same list returns the same verdict."""

    warnings = [_w("high"), _w("medium"), _w("low"), _w("low")]
    first = baseline_reviewability(warnings)
    second = baseline_reviewability(warnings)
    assert first == second == "FAIL"


def test_baseline_reviewability_is_pure_python_no_pydantic_validation() -> None:
    """Aggregation reads ``severity`` only; it should not re-validate models.

    We pass already-constructed Pydantic objects and assert that the
    function does not alter them or raise. This is a smoke test against
    accidental ``model_validate`` round-trips inside the hot path.
    """

    warnings = [_w("medium"), _w("medium")]
    before = [w.model_dump() for w in warnings]
    assert baseline_reviewability(warnings) == "WARN"
    after = [w.model_dump() for w in warnings]
    assert before == after
