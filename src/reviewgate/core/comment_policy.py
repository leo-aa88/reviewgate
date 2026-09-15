"""`policy.code_comments` configuration models (issue #143).

These models live beside :mod:`reviewgate.core.code_comments` rather than in
:mod:`reviewgate.core.config` so the shared `Policy` block stays a thin
collection of toggles. They are re-exported from
:mod:`reviewgate.core.config` for backwards-compatible imports; the
canonical home is this module.

Pure: stdlib plus Pydantic only (§4.1 boundary).
"""

from __future__ import annotations

from pydantic import Field, model_validator

from ._base import StrictModel


class CodeCommentWarnThresholds(StrictModel):
    """`policy.code_comments.warn` block (issue #143)."""

    max_block_lines: int = Field(
        default=10,
        ge=0,
        description=(
            "Warn when a newly-added consecutive full-line comment block "
            "reaches this many lines (issue #143)."
        ),
    )
    max_total_lines: int = Field(
        default=60,
        ge=0,
        description=(
            "Warn when newly-added full-line comment lines across eligible "
            "source files reach this count (issue #143)."
        ),
    )
    max_comment_ratio: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description=(
            "Warn when the ratio of newly-added comment lines to newly-added "
            "non-blank source lines reaches this value (issue #143)."
        ),
    )


class CodeCommentFailThresholds(StrictModel):
    """`policy.code_comments.fail` block (issue #143)."""

    max_block_lines: int = Field(
        default=25,
        ge=0,
        description=(
            "Escalate to a high-severity warning when a newly-added "
            "consecutive full-line comment block reaches this many lines "
            "(issue #143)."
        ),
    )
    max_total_lines: int = Field(
        default=150,
        ge=0,
        description=(
            "Escalate to a high-severity warning when newly-added full-line "
            "comment lines across eligible source files reach this count "
            "(issue #143)."
        ),
    )
    max_comment_ratio: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description=(
            "Escalate to a high-severity warning when the ratio of "
            "newly-added comment lines to newly-added non-blank source "
            "lines reaches this value (issue #143)."
        ),
    )


class CodeCommentPolicy(StrictModel):
    """`policy.code_comments` block (issue #143).

    Configures the deterministic excessive-comment-verbosity heuristic in
    :mod:`reviewgate.core.code_comments`. The heuristic measures objective
    volume signals only (block size, total lines, comment-to-code ratio);
    it never judges comment usefulness, correctness, or authorship.
    """

    enabled: bool = Field(
        default=True,
        description="When false, no code-comment warnings or stats are emitted (issue #143).",
    )
    warn: CodeCommentWarnThresholds = Field(
        default_factory=CodeCommentWarnThresholds,
        description="Warn thresholds; missing keys fall back to the issue #143 defaults.",
    )
    fail: CodeCommentFailThresholds = Field(
        default_factory=CodeCommentFailThresholds,
        description="Fail thresholds; missing keys fall back to the issue #143 defaults.",
    )
    min_added_source_lines: int = Field(
        default=20,
        ge=0,
        description=(
            "Minimum number of newly-added non-blank source lines before the "
            "comment-ratio dimension may trigger; block and total-volume "
            "dimensions stay active below this sample size (issue #143)."
        ),
    )

    @model_validator(mode="after")
    def _warn_thresholds_do_not_exceed_fail(self) -> CodeCommentPolicy:
        """Reject configs where any warn threshold is looser than its fail twin."""

        pairs = (
            ("max_block_lines", self.warn.max_block_lines, self.fail.max_block_lines),
            ("max_total_lines", self.warn.max_total_lines, self.fail.max_total_lines),
            (
                "max_comment_ratio",
                self.warn.max_comment_ratio,
                self.fail.max_comment_ratio,
            ),
        )
        for name, warn_value, fail_value in pairs:
            if warn_value > fail_value:
                raise ValueError(
                    f"policy.code_comments: warn.{name} ({warn_value}) must not "
                    f"exceed fail.{name} ({fail_value})"
                )
        return self


__all__ = [
    "CodeCommentFailThresholds",
    "CodeCommentPolicy",
    "CodeCommentWarnThresholds",
]
