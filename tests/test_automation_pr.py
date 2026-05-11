"""Tests for :mod:`reviewgate.core.automation_pr` (DESIGN §10.4.1 / §10.4.2)."""

from __future__ import annotations

import pytest

from reviewgate.core.automation_pr import (
    classify_pr_author_login,
    finalize_size_stats_for_pr_author,
    is_known_dependency_automation_login,
    is_manifest_only_dependency_automation_pr,
)
from reviewgate.core.schemas import FileCategoryRow
from reviewgate.core.size import SizeStats


def _row(
    filename: str,
    *,
    categories: tuple[str, ...],
    changes: int = 1,
) -> FileCategoryRow:
    return FileCategoryRow(
        filename=filename,
        categories=list(categories),  # type: ignore[arg-type]
        risky=False,
        human_authored=True,
        changes=changes,
    )


@pytest.mark.parametrize(
    ("login", "expected"),
    [
        ("dependabot[bot]", "dependency_automation"),
        ("DEPENDABOT[bot]", "dependency_automation"),
        ("renovate-bot", "dependency_automation"),
        ("Copilot", "coding_agent_automation"),
        ("copilot[bot]", "coding_agent_automation"),
        ("cursor[bot]", "coding_agent_automation"),
        ("github-actions[bot]", "generic_automation"),
        ("octocat", "human"),
        ("", "human"),
        ("   ", "human"),
    ],
)
def test_classify_pr_author_login(login: str, expected: str) -> None:
    assert classify_pr_author_login(login) == expected


@pytest.mark.parametrize(
    "login,expected",
    [
        ("dependabot[bot]", True),
        ("renovate[bot]", True),
        ("renovate-bot", True),
        ("  dependabot[bot]  ", True),
        ("octocat", False),
        ("Copilot", False),
        ("", False),
    ],
)
def test_is_known_dependency_automation_login(login: str, expected: bool) -> None:
    assert is_known_dependency_automation_login(login) is expected


def test_manifest_only_requires_dependency_or_lockfile_per_row() -> None:
    author = "dependabot[bot]"
    rows = [_row("README.md", categories=("docs",))]
    assert not is_manifest_only_dependency_automation_pr(author, rows)


def test_manifest_only_rejects_source_files() -> None:
    author = "dependabot[bot]"
    rows = [
        _row("requirements.txt", categories=("dependency",)),
        _row("src/a.py", categories=("source",)),
    ]
    assert not is_manifest_only_dependency_automation_pr(author, rows)


def test_manifest_only_accepts_lockfile_and_manifest_mix() -> None:
    author = "dependabot[bot]"
    rows = [
        _row("pyproject.toml", categories=("dependency",)),
        _row("uv.lock", categories=("lockfile",)),
    ]
    assert is_manifest_only_dependency_automation_pr(author, rows)


def test_finalize_clamps_human_loc_for_dependabot_manifest_pr() -> None:
    base = SizeStats(
        raw_loc_changed=10,
        excluded_loc_changed=8,
        human_loc_changed=2,
        files_changed=2,
        additions=6,
        deletions=4,
    )
    rows = [
        _row("requirements.txt", categories=("dependency", "test"), changes=2),
        _row("poetry.lock", categories=("lockfile",), changes=8),
    ]
    stats, extra = finalize_size_stats_for_pr_author(
        base,
        author="dependabot[bot]",
        file_categories=rows,
    )
    assert stats.human_loc_changed == 0
    assert stats.excluded_loc_changed == 10
    assert extra["dependency_automation_manifest_only"] is True
    assert extra["pr_author_kind"] == "dependency_automation"
    assert extra["pr_author_login"] == "dependabot[bot]"


def test_finalize_leaves_human_authors_untouched() -> None:
    base = SizeStats(
        raw_loc_changed=10,
        excluded_loc_changed=8,
        human_loc_changed=2,
        files_changed=2,
        additions=6,
        deletions=4,
    )
    rows = [_row("requirements.txt", categories=("dependency",), changes=2)]
    stats, extra = finalize_size_stats_for_pr_author(
        base,
        author="octocat",
        file_categories=rows,
    )
    assert stats == base
    assert "dependency_automation_manifest_only" not in extra
    assert extra["pr_author_kind"] == "human"
    assert extra["pr_author_login"] == "octocat"


def test_finalize_tags_copilot_without_manifest_override() -> None:
    base = SizeStats(
        raw_loc_changed=5,
        excluded_loc_changed=0,
        human_loc_changed=5,
        files_changed=1,
        additions=3,
        deletions=2,
    )
    rows = [_row("src/a.py", categories=("source",), changes=5)]
    stats, extra = finalize_size_stats_for_pr_author(
        base,
        author="Copilot",
        file_categories=rows,
    )
    assert stats == base
    assert extra["pr_author_kind"] == "coding_agent_automation"
    assert extra["pr_author_login"] == "Copilot"
