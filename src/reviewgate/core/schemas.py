"""Normalized JSON schemas for the deterministic engine.

Covers docs/DESIGN.md \u00a710.1 (input envelope), \u00a710.2 (output
envelope and ``split_hints`` items), \u00a710.5 (per-file
``file_categories`` row), and \u00a710.12 (warning objects). The typed
``.reviewgate.yml`` configuration model lives in
:mod:`reviewgate.core.config`; ``EngineInput.config`` here remains a
JSON-shaped passthrough so that fixtures already serialized as JSON can
be loaded without re-validating \u00a712 fields twice.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from reviewgate.core._base import StrictModel

Reviewability = Literal["PASS", "WARN", "FAIL"]
FileStatus = Literal["added", "modified", "removed", "renamed"]
WarningSeverity = Literal["low", "medium", "high"]

FileCategory = Literal[
    "source",
    "test",
    "docs",
    "config",
    "dependency",
    "lockfile",
    "migration",
    "infra",
    "auth",
    "billing",
    "generated",
    "snapshot",
    "vendored",
    "minified",
    "asset",
    "unknown",
]


class PRRecord(StrictModel):
    """Pull request subset passed into the engine (\u00a710.1)."""

    title: str = Field(description="PR title (\u00a710.1).")
    body: str = Field(description="PR body (\u00a710.1).")
    author: str = Field(description="PR author login or handle (\u00a710.1).")
    base_branch: str = Field(description="Base branch name (\u00a710.1).")
    head_branch: str = Field(description="Head branch name (\u00a710.1).")
    additions: int = Field(ge=0, description="Line additions count from GitHub API (\u00a710.1).")
    deletions: int = Field(ge=0, description="Line deletions count from GitHub API (\u00a710.1).")
    changed_files: int = Field(ge=0, description="Count of changed files (\u00a710.1).")


class ChangedFile(StrictModel):
    """One entry in the changed-files list (\u00a710.1)."""

    filename: str = Field(description="Repository-relative path (\u00a710.1).")
    status: FileStatus = Field(description="File change status (\u00a710.1).")
    additions: int = Field(ge=0, description="Line additions for this file (\u00a710.1).")
    deletions: int = Field(ge=0, description="Line deletions for this file (\u00a710.1).")
    changes: int = Field(ge=0, description="Total changed lines for this file (\u00a710.1).")
    patch: str | None = Field(
        default=None,
        description="Optional unified diff text when available (\u00a710.1).",
    )


class EngineInput(StrictModel):
    """Top-level deterministic engine input envelope (\u00a710.1)."""

    pr: PRRecord = Field(description="Normalized PR metadata (\u00a710.1).")
    files: list[ChangedFile] = Field(
        description="Changed files with optional patches (\u00a710.1)."
    )
    config: dict[str, JsonValue] = Field(
        default_factory=dict,
        description=(
            "Effective repo config (\u00a710.1 `config`); pass the JSON-mode dump of "
            ":class:`reviewgate.core.config.ReviewGateConfig` for typed defaults."
        ),
    )


class EngineWarning(StrictModel):
    """Single deterministic warning (\u00a710.12)."""

    code: str = Field(
        description="Stable machine-readable warning identifier (\u00a710.12).",
    )
    severity: WarningSeverity = Field(description="Warning severity (\u00a710.12).")
    message: str = Field(description="Human-readable summary (\u00a710.12).")
    evidence: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Structured evidence payload; JSON-serializable values only (\u00a710.12).",
    )


class FileCategoryRow(StrictModel):
    """Per-file categorization row in the report (\u00a710.5 example, \u00a710.2 ``file_categories``)."""

    filename: str = Field(description="Repository-relative path (\u00a710.5).")
    categories: list[FileCategory] = Field(
        min_length=1,
        description="One or more categories from the \u00a710.5 closed set.",
    )
    risky: bool = Field(description="Whether the path matches risky heuristics (\u00a710.5).")
    human_authored: bool = Field(
        description=(
            "When ``False``, this file's ``changes`` are subtracted in "
            "\u00a710.4 ``excluded_loc_changed`` (lockfiles, generated, "
            "snapshot, vendored, minified). Known dependency bots may "
            "receive an additional manifest-only adjustment "
            "(:mod:`reviewgate.core.automation_pr`). The JSON field "
            "``human_loc_changed`` names the post-processing remainder used "
            "for §10.3 thresholds."
        ),
    )
    changes: int = Field(ge=0, description="Changed line count used for reporting (\u00a710.5).")


class SplitHint(StrictModel):
    """Structured split suggestion item (\u00a710.2 `split_hints`)."""

    title: str = Field(description="Short title for a suggested follow-up PR (\u00a710.2).")
    scope: str | None = Field(
        default=None,
        description="Optional scope description; use null when absent (\u00a710.2).",
    )


class ReviewabilityReport(StrictModel):
    """Deterministic engine output (\u00a710.2)."""

    reviewability: Reviewability = Field(description="Baseline PASS/WARN/FAIL (\u00a710.2).")
    stats: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Aggregated numeric stats for reporting (\u00a710.2 `stats`).",
    )
    warnings: list[EngineWarning] = Field(
        default_factory=list,
        description="Deterministic warnings (\u00a710.2, \u00a710.12).",
    )
    suggested_labels: list[str] = Field(
        default_factory=list,
        description="Suggested GitHub labels (\u00a710.2); values validated elsewhere when applied.",
    )
    file_categories: list[FileCategoryRow] = Field(
        default_factory=list,
        description="Per-file categorization rows (\u00a710.2, \u00a710.5).",
    )
    split_hints: list[SplitHint] = Field(
        default_factory=list,
        description="Suggested PR splits (\u00a710.2 `split_hints`).",
    )
    reviewer_checklist: list[str] = Field(
        default_factory=list,
        description="Checklist strings for reviewers (\u00a710.2).",
    )


__all__ = [
    "ChangedFile",
    "EngineInput",
    "EngineWarning",
    "FileCategory",
    "FileCategoryRow",
    "FileStatus",
    "PRRecord",
    "Reviewability",
    "ReviewabilityReport",
    "SplitHint",
    "WarningSeverity",
]
