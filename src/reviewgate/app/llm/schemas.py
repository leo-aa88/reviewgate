"""Structured LLM output matching ``docs/DESIGN.md`` §11.7 (issue #58)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from reviewgate.core.schemas import Reviewability

_LlmIssueSeverity = Literal["low", "medium", "high"]


class LlmIssueItem(BaseModel):
    """Single structured issue row from the LLM report."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    severity: _LlmIssueSeverity
    title: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    suggested_fix: str = Field(min_length=1)


class LlmSplitSuggestionItem(BaseModel):
    """LLM split suggestion (§11.7 ``split_suggestions``)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    scope: str = Field(min_length=1)


class LlmReviewabilityReport(BaseModel):
    """Provider-validated JSON shape for hosted reviewability LLM output (§11.7)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reviewability: Reviewability
    summary: str = Field(min_length=1)
    issues: list[LlmIssueItem] = Field(default_factory=list)
    suggested_labels: list[str] = Field(default_factory=list)
    split_suggestions: list[LlmSplitSuggestionItem] = Field(default_factory=list)
    reviewer_checklist: list[str] = Field(default_factory=list)
