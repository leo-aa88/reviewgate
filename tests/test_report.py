"""Tests for :mod:`reviewgate.core.report` against \u00a710.2 + \u00a713.9.

Locks the closed mapping from warning codes to label names:

* Verdict label is always first and matches PASS / WARN / FAIL.
* Concern labels appear in spec enumeration order
  (``too_large``, ``missing_context``, ``risky_change``, ``needs_split``).
* Concerns are deduplicated: two size warnings yield ONE
  ``too_large`` label.
* User-overridden label names propagate from
  :class:`reviewgate.core.config.Labels`.
"""

from __future__ import annotations

import pytest

from reviewgate.core.config import Labels
from reviewgate.core.linked_issue import WARN_CODE_MISSING_LINKED_ISSUE
from reviewgate.core.mixed_concern import WARN_CODE_MIXED_CONCERN
from reviewgate.core.pr_body import WARN_CODE_WEAK_BODY
from reviewgate.core.report import suggested_labels
from reviewgate.core.risky_paths import WARN_CODE_RISKY_NO_RATIONALE
from reviewgate.core.schemas import EngineWarning, Reviewability, WarningSeverity
from reviewgate.core.size import (
    WARN_CODE_TOO_LARGE_HUMAN_LOC,
    WARN_CODE_TOO_MANY_FILES,
)


def _warning(code: str, severity: WarningSeverity = "medium") -> EngineWarning:
    return EngineWarning(
        code=code,
        severity=severity,
        message="x",
        evidence={},
    )


# --- verdict labels --------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        pytest.param("PASS", "reviewability-pass", id="pass"),
        pytest.param("WARN", "reviewability-warn", id="warn"),
        pytest.param("FAIL", "reviewability-fail", id="fail"),
    ],
)
def test_verdict_label_uses_default_label_names(
    verdict: Reviewability,
    expected: str,
) -> None:
    """Default :class:`Labels` matches \u00a712 verbatim."""

    assert suggested_labels(verdict, [], Labels()) == [expected]


def test_verdict_label_is_always_first() -> None:
    """The verdict label appears at index 0 even when concerns fire."""

    out = suggested_labels(
        "WARN",
        [_warning(WARN_CODE_TOO_MANY_FILES, severity="medium")],
        Labels(),
    )
    assert out[0] == "reviewability-warn"


# --- concern -> label mapping ---------------------------------------------


@pytest.mark.parametrize(
    ("warning_code", "expected_label"),
    [
        pytest.param(WARN_CODE_TOO_MANY_FILES, "too-large", id="too-many-files"),
        pytest.param(WARN_CODE_TOO_LARGE_HUMAN_LOC, "too-large", id="too-large-loc"),
        pytest.param(WARN_CODE_WEAK_BODY, "missing-context", id="weak-body"),
        pytest.param(
            WARN_CODE_MISSING_LINKED_ISSUE,
            "missing-context",
            id="missing-linked-issue",
        ),
        pytest.param(
            WARN_CODE_RISKY_NO_RATIONALE,
            "risky-change",
            id="risky-paths",
        ),
        pytest.param(WARN_CODE_MIXED_CONCERN, "needs-split", id="mixed-concern"),
    ],
)
def test_each_warning_code_maps_to_expected_label(
    warning_code: str,
    expected_label: str,
) -> None:
    """\u00a713.9 mapping is exhaustive across the five Milestone-2 heuristics."""

    out = suggested_labels("PASS", [_warning(warning_code)], Labels())
    assert expected_label in out


def test_size_warnings_collapse_to_single_too_large_label() -> None:
    """Both size codes share the same concern, so the label is added once."""

    out = suggested_labels(
        "WARN",
        [
            _warning(WARN_CODE_TOO_MANY_FILES),
            _warning(WARN_CODE_TOO_LARGE_HUMAN_LOC, severity="high"),
        ],
        Labels(),
    )
    assert out.count("too-large") == 1


def test_context_warnings_collapse_to_single_missing_context_label() -> None:
    """Weak body + missing linked issue share ``missing_context``."""

    out = suggested_labels(
        "WARN",
        [
            _warning(WARN_CODE_WEAK_BODY),
            _warning(WARN_CODE_MISSING_LINKED_ISSUE),
        ],
        Labels(),
    )
    assert out.count("missing-context") == 1


# --- ordering, dedup, completeness -----------------------------------------


def test_concern_labels_appear_in_spec_enumeration_order() -> None:
    """Order is: verdict, too_large, missing_context, risky_change, needs_split.

    Locked so reviewers see a stable label list across runs and so
    downstream label-application code (#52) can rely on the order.
    """

    warnings = [
        _warning(WARN_CODE_MIXED_CONCERN),  # needs_split
        _warning(WARN_CODE_RISKY_NO_RATIONALE, severity="high"),  # risky_change
        _warning(WARN_CODE_WEAK_BODY),  # missing_context
        _warning(WARN_CODE_TOO_MANY_FILES),  # too_large
    ]
    out = suggested_labels("FAIL", warnings, Labels())
    assert out == [
        "reviewability-fail",
        "too-large",
        "missing-context",
        "risky-change",
        "needs-split",
    ]


def test_no_warnings_yields_only_verdict_label() -> None:
    assert suggested_labels("PASS", [], Labels()) == ["reviewability-pass"]


def test_unknown_warning_codes_are_ignored() -> None:
    """A warning whose code is not in the rule table contributes no label."""

    out = suggested_labels(
        "WARN",
        [_warning("something_brand_new")],
        Labels(),
    )
    assert out == ["reviewability-warn"]


# --- user-config overrides -------------------------------------------------


def test_user_overridden_labels_propagate() -> None:
    """\u00a712: custom names from ``.reviewgate.yml`` flow through unchanged."""

    custom = Labels.model_validate(
        {
            "pass": "rg/pass",
            "warn": "rg/warn",
            "fail": "rg/fail",
            "too_large": "rg/too-large",
            "missing_context": "rg/missing-context",
            "risky_change": "rg/risky",
            "needs_split": "rg/split",
        },
    )
    out = suggested_labels(
        "FAIL",
        [
            _warning(WARN_CODE_TOO_MANY_FILES),
            _warning(WARN_CODE_RISKY_NO_RATIONALE, severity="high"),
            _warning(WARN_CODE_MIXED_CONCERN),
        ],
        custom,
    )
    assert out == [
        "rg/fail",
        "rg/too-large",
        "rg/risky",
        "rg/split",
    ]


def test_verdict_label_not_duplicated_when_collision_with_concern_label() -> None:
    """If a user names the verdict label same as a concern label, dedupe wins.

    Edge case: a misconfigured ``.reviewgate.yml`` could set
    ``labels.warn`` and ``labels.too_large`` to the same string. The
    function must still return a valid list without duplicates.
    """

    collide = Labels(warn="overlap", too_large="overlap")
    out = suggested_labels(
        "WARN",
        [_warning(WARN_CODE_TOO_MANY_FILES)],
        collide,
    )
    assert out == ["overlap"]
