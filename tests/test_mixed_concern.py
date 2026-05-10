"""Tests for :mod:`reviewgate.core.mixed_concern` against \u00a710.11.

\u00a710.11 explicitly lists "normal" combinations that must NOT trip the
heuristic and "suspicious" combinations that should. These tests pin
both directions so a future broadening of the rule cannot silently
create alarm fatigue (the spec's stated failure mode).
"""

from __future__ import annotations

from typing import Sequence

import pytest

from reviewgate.core.mixed_concern import (
    WARN_CODE_MIXED_CONCERN,
    mixed_concern_warning,
)
from reviewgate.core.schemas import FileCategory, FileCategoryRow


def _row(
    filename: str,
    *,
    categories: Sequence[FileCategory],
    risky: bool = False,
    human_authored: bool = True,
) -> FileCategoryRow:
    return FileCategoryRow(
        filename=filename,
        categories=list(categories),
        risky=risky,
        human_authored=human_authored,
        changes=5,
    )


# --- §10.11 "normal" combinations: NO warning -----------------------------


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param(
            [
                _row("src/utils.py", categories=["source"]),
                _row("tests/test_utils.py", categories=["test", "source"]),
            ],
            id="source-plus-tests",
        ),
        pytest.param(
            [
                _row("src/utils.py", categories=["source"]),
                _row("tests/test_utils.py", categories=["test", "source"]),
                _row("docs/usage.md", categories=["docs"]),
            ],
            id="source-plus-tests-plus-docs",
        ),
        pytest.param(
            [
                _row("src/utils.py", categories=["source"]),
                _row("tests/test_utils.py", categories=["test", "source"]),
                _row("config/settings.yaml", categories=["config"]),
            ],
            id="source-plus-tests-plus-config",
        ),
        pytest.param(
            [
                _row("backend/migrations/001.sql", categories=["source", "migration"], risky=True),
                _row("src/models.py", categories=["source"]),
                _row("tests/test_models.py", categories=["test", "source"]),
            ],
            id="single-risk-category-migration-plus-source-plus-tests",
        ),
        pytest.param(
            [
                _row("infra/k8s/deploy.yaml", categories=["infra"], risky=True),
                _row("src/feature.py", categories=["source"]),
                _row("tests/test_feature.py", categories=["test", "source"]),
            ],
            id="single-risk-category-infra-plus-source-plus-tests",
        ),
        pytest.param(
            [
                _row("services/auth/sso.py", categories=["source", "auth"], risky=True),
                _row("billing/invoice.py", categories=["source", "billing"], risky=True),
                _row("tests/test_auth.py", categories=["test", "source"]),
            ],
            id="two-risk-categories-only",
        ),
    ],
)
def test_normal_combinations_emit_no_warning(rows: list[FileCategoryRow]) -> None:
    """\u00a710.11: normal feature PRs touching <3 risk categories pass."""

    assert mixed_concern_warning(rows) is None


def test_empty_file_list_emits_no_warning() -> None:
    assert mixed_concern_warning([]) is None


def test_pure_diversity_does_not_trip_heuristic() -> None:
    """A PR with source/test/docs/config/dependency emits no warning.

    The spec is explicit: "do not simply fail because many categories
    are touched." Five non-risk categories together stay silent.
    """

    rows = [
        _row("src/x.py", categories=["source"]),
        _row("tests/test_x.py", categories=["test", "source"]),
        _row("docs/x.md", categories=["docs"]),
        _row("config/settings.yaml", categories=["config"]),
        _row("package.json", categories=["dependency"]),
    ]
    assert mixed_concern_warning(rows) is None


# --- §10.11 "suspicious" combinations: warning ----------------------------


def test_billing_auth_infra_combination_emits_warning() -> None:
    """\u00a710.11 suspicious example #1 verbatim."""

    rows = [
        _row("services/auth/sso.py", categories=["source", "auth"], risky=True),
        _row("billing/invoice.py", categories=["source", "billing"], risky=True),
        _row("infra/k8s/deploy.yaml", categories=["infra"], risky=True),
    ]
    warning = mixed_concern_warning(rows)
    assert warning is not None
    assert warning.code == WARN_CODE_MIXED_CONCERN
    assert warning.severity == "medium"
    assert warning.evidence["risk_categories_touched"] == ["auth", "billing", "infra"]
    assert warning.evidence["count"] == 3
    assert warning.evidence["threshold"] == 3


def test_migration_infra_auth_combination_emits_warning() -> None:
    """\u00a710.11 suspicious example #2: migration + workflow (infra) + UI (auth)."""

    rows = [
        _row("backend/migrations/001.sql", categories=["source", "migration"], risky=True),
        _row(".github/workflows/ci.yml", categories=["infra"], risky=True),
        _row("services/auth/login.py", categories=["source", "auth"], risky=True),
    ]
    warning = mixed_concern_warning(rows)
    assert warning is not None
    assert warning.evidence["count"] == 3


def test_four_risk_categories_emits_warning_with_full_list() -> None:
    """All four risk categories at once is the strongest mixed-concern signal."""

    rows = [
        _row("services/auth/sso.py", categories=["source", "auth"], risky=True),
        _row("billing/invoice.py", categories=["source", "billing"], risky=True),
        _row("infra/k8s/deploy.yaml", categories=["infra"], risky=True),
        _row("backend/migrations/001.sql", categories=["source", "migration"], risky=True),
    ]
    warning = mixed_concern_warning(rows)
    assert warning is not None
    assert warning.evidence["risk_categories_touched"] == [
        "auth",
        "billing",
        "infra",
        "migration",
    ]
    assert warning.evidence["count"] == 4


def test_warning_message_lists_categories_alphabetically() -> None:
    """Stable surface across runs."""

    rows = [
        _row("infra/k8s/deploy.yaml", categories=["infra"], risky=True),
        _row("services/auth/sso.py", categories=["source", "auth"], risky=True),
        _row("backend/migrations/001.sql", categories=["source", "migration"], risky=True),
    ]
    warning = mixed_concern_warning(rows)
    assert warning is not None
    assert "auth, infra, migration" in warning.message


def test_two_risk_categories_below_threshold_silently_passes() -> None:
    """Boundary: 2 risk categories must NOT trip the heuristic.

    The threshold is `>= 3` and this is a deliberate trade-off against
    false positives -- two simultaneous concerns is not yet the
    "unrelated cluster" signal \u00a710.11 calls out.
    """

    rows = [
        _row("services/auth/sso.py", categories=["source", "auth"], risky=True),
        _row("billing/invoice.py", categories=["source", "billing"], risky=True),
    ]
    assert mixed_concern_warning(rows) is None


def test_categorizer_can_return_generator() -> None:
    """Function consumes generator inputs once without losing data."""

    rows = [
        _row("services/auth/sso.py", categories=["source", "auth"], risky=True),
        _row("billing/invoice.py", categories=["source", "billing"], risky=True),
        _row("infra/k8s/deploy.yaml", categories=["infra"], risky=True),
    ]

    def gen() -> object:
        yield from rows

    warning = mixed_concern_warning(gen())  # type: ignore[arg-type]
    assert warning is not None
    assert warning.evidence["count"] == 3
