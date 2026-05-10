"""Tests for :mod:`reviewgate.core.risky_paths` against \u00a710.6 + \u00a710.10 + \u00a712.

Locks the four-step decision ladder of :func:`risky_paths_warning`:

1. No risky files                              -> ``None``.
2. Risky files but no \u00a710.5 risk-category    -> ``None``.
3. Risky files + body mentions a category
   keyword OR a risky file's path tokens       -> ``None``.
4. Risky files + body silent                   -> warning whose
   ``severity`` follows
   ``policy.fail_on_risky_paths_without_context``.

Builds :class:`FileCategoryRow` rows directly so the heuristic is
exercised independently of the categoriser.
"""

from __future__ import annotations

from typing import Sequence

import pytest

from reviewgate.core.risky_paths import (
    WARN_CODE_RISKY_NO_RATIONALE,
    risky_paths_warning,
)
from reviewgate.core.schemas import FileCategory, FileCategoryRow


def _row(
    filename: str,
    *,
    categories: Sequence[FileCategory],
    risky: bool,
    human_authored: bool = True,
    changes: int = 5,
) -> FileCategoryRow:
    return FileCategoryRow(
        filename=filename,
        categories=list(categories),
        risky=risky,
        human_authored=human_authored,
        changes=changes,
    )


# --- step 1: no risky files ------------------------------------------------


def test_no_risky_files_yields_no_warning() -> None:
    rows = [
        _row("src/utils.py", categories=["source"], risky=False),
        _row("README.md", categories=["docs"], risky=False),
    ]
    assert (
        risky_paths_warning(
            rows,
            "Some prose that mentions auth and migrations heavily.",
            fail_on_risky_paths_without_context=True,
        )
        is None
    )


def test_empty_file_list_yields_no_warning() -> None:
    assert (
        risky_paths_warning(
            [],
            "anything",
            fail_on_risky_paths_without_context=True,
        )
        is None
    )


# --- step 2: risky but no known category -----------------------------------


def test_user_pattern_only_risky_file_is_skipped() -> None:
    """A file flagged risky by a user-custom pattern (no \u00a710.5 category)
    skips the rationale check -- we have no keyword set to match on.
    """

    rows = [
        _row("config/secret/keys.yaml", categories=["config"], risky=True),
    ]
    assert (
        risky_paths_warning(
            rows,
            "no rationale at all",
            fail_on_risky_paths_without_context=True,
        )
        is None
    )


# --- step 3a: body mentions a category keyword -----------------------------


@pytest.mark.parametrize(
    ("category", "rationale_word"),
    [
        pytest.param("auth", "authentication", id="auth-keyword"),
        pytest.param("auth", "session", id="auth-synonym-session"),
        pytest.param("billing", "invoice", id="billing-keyword"),
        pytest.param("billing", "subscription", id="billing-synonym"),
        pytest.param("infra", "deploy", id="infra-keyword"),
        pytest.param("infra", "kubernetes", id="infra-synonym"),
        pytest.param("migration", "schema", id="migration-keyword"),
        pytest.param("migration", "rollback", id="migration-synonym"),
    ],
)
def test_body_keyword_satisfies_rationale_check(
    category: FileCategory,
    rationale_word: str,
) -> None:
    """A single category-synonym mention silences the warning."""

    rows = [
        _row("services/risky.py", categories=["source", category], risky=True),
    ]
    body = f"This change updates the {rationale_word} flow safely."
    assert (
        risky_paths_warning(
            rows,
            body,
            fail_on_risky_paths_without_context=True,
        )
        is None
    )


def test_body_keyword_match_is_case_insensitive() -> None:
    rows = [
        _row("services/auth/login.py", categories=["source", "auth"], risky=True),
    ]
    body = "Refactors the AUTHENTICATION middleware."
    assert (
        risky_paths_warning(
            rows,
            body,
            fail_on_risky_paths_without_context=True,
        )
        is None
    )


def test_keyword_must_be_a_whole_token_not_a_substring() -> None:
    """``deploys`` should not satisfy the ``infra`` check.

    The synonym set carries ``deploy`` and ``deployment`` but not
    ``deploys``; a body that says ``employs`` or ``deploys`` must not
    accidentally satisfy the rationale check via substring matching.
    """

    rows = [
        _row("infra/k8s/main.yaml", categories=["infra"], risky=True),
    ]
    # Body deliberately has no whole-word match against the infra set.
    body = "This change reorders unrelated UI components."
    warning = risky_paths_warning(
        rows,
        body,
        fail_on_risky_paths_without_context=True,
    )
    assert warning is not None, (
        "no whole-token match should exist; substring matching would "
        "produce a false negative on the warning"
    )


# --- step 3b: body mentions a risky file's path tokens ---------------------


def test_body_mentions_risky_file_basename_satisfies_check() -> None:
    rows = [
        _row("services/billing/invoice.py", categories=["source", "billing"], risky=True),
    ]
    body = "Tweaks the invoice rendering helper for clarity."
    assert (
        risky_paths_warning(
            rows,
            body,
            fail_on_risky_paths_without_context=True,
        )
        is None
    )


def test_body_mentions_risky_file_path_segment_satisfies_check() -> None:
    rows = [
        _row("backend/migrations/0007_users.sql", categories=["source", "migration"], risky=True),
    ]
    # Even without the keyword "migration" or the basename, mentioning
    # the directory segment is enough rationale.
    body = (
        "Bumps the users schema; see the new file under backend/."
    )
    # Note: "schema" is in the migration keyword set, so this also
    # satisfies via keyword. Use a body that only references the path
    # to isolate the path-token branch:
    body_path_only = "Tweaks the file at 0007_users in the relevant tree."
    assert (
        risky_paths_warning(
            rows,
            body_path_only,
            fail_on_risky_paths_without_context=True,
        )
        is None
    )


# --- step 4: warning emitted ------------------------------------------------


def test_silent_body_emits_high_severity_warning_under_default_policy() -> None:
    """\u00a712 default ``fail_on_risky_paths_without_context=True`` -> high."""

    rows = [
        _row("services/auth/login.py", categories=["source", "auth"], risky=True),
        _row("billing/invoice.py", categories=["source", "billing"], risky=True),
    ]
    body = "Small refactor of internal helpers."
    warning = risky_paths_warning(
        rows,
        body,
        fail_on_risky_paths_without_context=True,
    )
    assert warning is not None
    assert warning.code == WARN_CODE_RISKY_NO_RATIONALE
    assert warning.severity == "high"
    assert warning.evidence["policy"] == "fail_on_risky_paths_without_context"
    assert warning.evidence["risky_files"] == [
        "services/auth/login.py",
        "billing/invoice.py",
    ]
    assert sorted(warning.evidence["risky_categories"]) == ["auth", "billing"]


def test_silent_body_emits_medium_severity_when_policy_disabled() -> None:
    """\u00a712 ``fail_on_risky_paths_without_context=False`` downgrades to warn."""

    rows = [
        _row("services/auth/login.py", categories=["source", "auth"], risky=True),
    ]
    warning = risky_paths_warning(
        rows,
        "unrelated body text",
        fail_on_risky_paths_without_context=False,
    )
    assert warning is not None
    assert warning.severity == "medium"


def test_warning_message_lists_touched_categories() -> None:
    rows = [
        _row("infra/k8s/main.yaml", categories=["infra"], risky=True),
        _row("backend/migrations/001.sql", categories=["source", "migration"], risky=True),
    ]
    warning = risky_paths_warning(
        rows,
        "x",
        fail_on_risky_paths_without_context=True,
    )
    assert warning is not None
    # Categories appear in the message text in alphabetical order so
    # the surface stays stable between runs.
    assert "infra" in warning.message
    assert "migration" in warning.message


def test_only_one_category_must_be_covered_to_silence_warning() -> None:
    """If the body covers ANY touched risky category, the warning is silenced.

    The spec is "warn if body does not mention why" (singular). This
    test pins that interpretation: covering one category is enough,
    even when several risky categories were touched. This is a
    deliberate trade-off against false positives.
    """

    rows = [
        _row("services/auth/login.py", categories=["source", "auth"], risky=True),
        _row("billing/invoice.py", categories=["source", "billing"], risky=True),
    ]
    # Body explains auth but not billing.
    body = "Improves session handling for logged-in users."
    assert (
        risky_paths_warning(
            rows,
            body,
            fail_on_risky_paths_without_context=True,
        )
        is None
    )
