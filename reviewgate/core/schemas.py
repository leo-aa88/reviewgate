"""Normalized JSON schemas for engine inputs and outputs (docs/DESIGN.md §10.1–§10.2, §10.12)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Reviewability = Literal["PASS", "WARN", "FAIL"]
FileStatus = Literal["added", "modified", "removed", "renamed"]
WarningSeverity = Literal["low", "medium", "high"]


class PRRecord(BaseModel):
    """Pull request subset passed into the engine (§10.1)."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    title: str
    body: str
    author: str
    base_branch: str
    head_branch: str
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changed_files: int = Field(ge=0)


class ChangedFile(BaseModel):
    """One entry in the changed-files list (§10.1)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    filename: str
    status: FileStatus
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    patch: str | None = None


class EngineInput(BaseModel):
    """Top-level deterministic engine input envelope (§10.1)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    pr: PRRecord
    files: list[ChangedFile]
    # Effective repo config merged with defaults lives here until `config.py` owns a typed model (#3).
    config: dict[str, Any] = Field(default_factory=dict)


class Warning(BaseModel):
    """Single deterministic warning (§10.12)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: str
    severity: WarningSeverity
    message: str
    evidence: dict[str, JsonValue] = Field(default_factory=dict)


class FileCategoryRow(BaseModel):
    """Per-file categorization row in the report (§10.5 JSON example, §10.2 `file_categories`)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    filename: str
    categories: list[str]
    risky: bool
    human_authored: bool
    changes: int = Field(ge=0)


class SplitHint(BaseModel):
    """Structured split suggestion item (§10.2 `split_hints`; shape aligned with §11.7 fields)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str
    scope: str = ""


class ReviewabilityReport(BaseModel):
    """Deterministic engine output (§10.2)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    reviewability: Reviewability
    stats: dict[str, JsonValue] = Field(default_factory=dict)
    warnings: list[Warning] = Field(default_factory=list)
    suggested_labels: list[str] = Field(default_factory=list)
    file_categories: list[FileCategoryRow] = Field(default_factory=list)
    split_hints: list[SplitHint] = Field(default_factory=list)
    reviewer_checklist: list[str] = Field(default_factory=list)


__all__ = [
    "ChangedFile",
    "EngineInput",
    "FileCategoryRow",
    "FileStatus",
    "PRRecord",
    "Reviewability",
    "ReviewabilityReport",
    "SplitHint",
    "Warning",
    "WarningSeverity",
]
