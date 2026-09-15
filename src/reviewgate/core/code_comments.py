"""Excessive code-comment verbosity heuristic (issue #143).

Detects *objective volume* signals in commentary a PR introduces, from the
optional unified diffs in :attr:`reviewgate.core.schemas.ChangedFile.patch`:

1. **Oversized consecutive comment blocks** -- the PR-wide maximum run of
   consecutive newly-added full-line comment lines (filename in evidence).
2. **Excessive total comment lines** -- newly-added full-line comment lines
   summed across all eligible files.
3. **Comment-heavy diff** -- the ratio of newly-added comment lines to
   newly-added non-blank source lines, guarded by a minimum sample size.

Explicit non-goals (issue #143): the heuristic never judges whether a
comment is useful, correct, well-written, or who or what wrote it. It
measures comment *volume*, never comment *value*.

Conservative-parsing contract (false negatives preferred over noisy false
positives):

* Only **added** patch lines contribute to metrics. Deleted lines are
  invisible: they are dropped before scanning, so they never terminate a
  comment block. Blank lines, code, and unchanged context lines terminate a
  block, because all three survive in the post-image between two added
  lines.
* A hunk that does not start at new-file line 0 or 1 begins at a lexical
  position the patch does not establish. It is analyzed only once its own
  context lines have established a normal code position; until then it
  contributes nothing rather than guessing.
* Only files the categorizer marks ``human_authored`` **and** ``source``
  are in scope. Docs, generated, vendored, minified, snapshot, asset,
  lockfile, manifest, and unknown-language files are skipped.
* Of those, only languages whose comment syntax the scanner actually models
  are classified (Python, Shell, JavaScript, TypeScript, Go; not JSX/TSX).
  An in-scope source file in any other language contributes its added
  non-blank lines to the ratio *denominator* only, so an unparsed language
  cannot inflate ``comment_ratio`` over the files the scanner can read.
* Inline (trailing) comments are **not** counted in this MVP, and neither
  are Python docstrings: docstrings are string literals and may be runtime
  data. Both decisions are documented false-negative trade-offs.
* The scanner tracks string literals (including JS/Go backtick strings,
  Python triple-quoted strings, and shell quotes and heredocs) so markers
  inside string content are never miscounted.

Each volume dimension emits at most one warning per PR (the same
convention as :mod:`reviewgate.core.size`): repeated evidence for one
metric must not masquerade as independent risk signals.

Syntax-level work lives in :mod:`reviewgate.core._comment_scan`; this module
owns the policy surface, the statistics model, and warning construction.

Pure: stdlib only, no I/O, no GitHub or LLM dependency (§4.1 boundary).
"""

from __future__ import annotations

from typing import Final

from pydantic import Field

from ._base import StrictModel
from ._comment_lex import _profile_for
from ._comment_scan import _count_added_source_lines, _tally_patch
from .comment_policy import CodeCommentPolicy
from .schemas import ChangedFile, EngineWarning, FileCategoryRow, WarningSeverity

# Stable warning codes (issue #143). One code per volume dimension; severity
# distinguishes warn vs fail so downstream consumers can dedupe by code, the
# same convention as :mod:`reviewgate.core.size`.
WARN_CODE_OVERSIZED_BLOCK: Final[str] = "oversized_comment_block"
"""A newly-added consecutive full-line comment block reaches a threshold."""

WARN_CODE_EXCESSIVE_LINES: Final[str] = "excessive_comment_lines"
"""Newly-added comment lines across eligible files reach a threshold."""

WARN_CODE_COMMENT_HEAVY: Final[str] = "comment_heavy_diff"
"""The comment-to-source ratio of added lines reaches a threshold."""

_RATIO_PRECISION: Final[int] = 4
"""Decimal places kept for ``comment_ratio``; rounding once keeps evidence
byte-identical across runs and platforms."""

_SEVERITY_FAIL: Final[WarningSeverity] = "high"
_SEVERITY_WARN: Final[WarningSeverity] = "medium"


class CommentStats(StrictModel):
    """PR-level added-comment volume totals (issue #143 ``CommentStats``).

    ``comment_ratio`` is ``comment_lines_added / source_lines_added``
    rounded to :data:`_RATIO_PRECISION` places, with ``0.0`` when no
    non-blank lines were added; ``source_lines_added`` is the sum of
    ``comment_lines_added`` and ``code_lines_added``, and includes in-scope
    source files whose comment syntax is not modeled.
    """

    comment_lines_added: int = Field(
        ge=0,
        description="Newly-added full-line comment lines across eligible files.",
    )
    code_lines_added: int = Field(
        ge=0,
        description="Newly-added non-blank lines in eligible files that are not comments.",
    )
    largest_comment_block_lines: int = Field(
        ge=0,
        description="Largest consecutive full-line comment block in any eligible file.",
    )
    comment_ratio: float = Field(
        ge=0.0,
        le=1.0,
        description="comment_lines_added / source_lines_added, rounded deterministically.",
    )


def _is_eligible_source(row: FileCategoryRow) -> bool:
    """True when the categorizer puts a file in this heuristic's scope.

    Eligibility reuses the categorizer's verdict rather than a second
    notion of "human-authored": the file must be categorised ``source``
    (docs, assets, configs, manifests, lockfiles are not), must not carry
    the ``docs`` label, and must be ``human_authored`` (generated, vendored,
    minified, snapshot, and lockfile rows are excluded).
    """

    return row.human_authored and "docs" not in row.categories and "source" in row.categories


class CommentAnalysis(StrictModel):
    """Result of :func:`analyze_added_comments`."""

    stats: CommentStats = Field(description="PR-level added-comment volume totals.")
    warnings: list[EngineWarning] = Field(
        default_factory=list,
        description="Deterministic code-comment warnings in stable order.",
    )


def analyze_added_comments(
    files: list[ChangedFile],
    file_categories: list[FileCategoryRow],
    policy: CodeCommentPolicy,
) -> CommentAnalysis:
    """Analyze added comment volume and map it to deterministic warnings.

    Args:
        files: The engine's active (post-``ignored_paths``) changed files,
            in engine order.
        file_categories: The categorizer rows for exactly those files, in
            the same order (the pairing the engine already maintains).
        policy: The ``policy.code_comments`` block from
            :class:`reviewgate.core.comment_policy.CodeCommentPolicy`.

    Returns:
        A :class:`CommentAnalysis` whose ``warnings`` are ordered: at most
        one ``oversized_comment_block`` for the PR-wide maximum (filename
        of the first file that attains it, in input order), then
        ``excessive_comment_lines``, then ``comment_heavy_diff``.
        Thresholds are inclusive lower bounds, matching
        :func:`reviewgate.core.size.size_warnings`.

    Raises:
        ValueError: If ``files`` and ``file_categories`` lengths differ, or
            any paired ``filename`` values differ (the public API must not
            apply one file's category verdict to another file's patch).
    """

    if len(files) != len(file_categories):
        raise ValueError(
            "code_comments: files and file_categories must pair one-to-one "
            f"(got {len(files)} files vs {len(file_categories)} rows)"
        )

    total_comment = 0
    total_code = 0
    largest_block = 0
    largest_block_file: str | None = None
    for file, row in zip(files, file_categories, strict=True):
        if file.filename != row.filename:
            raise ValueError(
                "code_comments: files and file_categories must pair by "
                f"filename (got {file.filename!r} vs {row.filename!r})"
            )
        if not _is_eligible_source(row) or file.patch is None:
            continue
        profile = _profile_for(file.filename)
        if profile is None:
            # In-scope source whose comment syntax is not modeled: it cannot
            # supply comment lines, but its added lines are real source
            # lines and must stay in the ratio denominator. Dropping them
            # would inflate comment_ratio over the readable files and make
            # the false positive issue #143 forbids.
            total_code += _count_added_source_lines(file.patch)
            continue
        tally = _tally_patch(file.patch, profile)
        total_comment += tally.comment_lines
        total_code += tally.code_lines
        if tally.largest_block > largest_block:
            largest_block = tally.largest_block
            largest_block_file = file.filename

    stats = CommentStats(
        comment_lines_added=total_comment,
        code_lines_added=total_code,
        largest_comment_block_lines=largest_block,
        comment_ratio=_ratio(total_comment, total_comment + total_code),
    )

    warnings: list[EngineWarning] = []
    if largest_block_file is not None:
        warning = _block_warning(largest_block_file, largest_block, policy)
        if warning is not None:
            warnings.append(warning)
    warnings.extend(_total_warning(total_comment, policy))
    warnings.extend(_ratio_warning(stats, policy))

    return CommentAnalysis(stats=stats, warnings=warnings)


def _ratio(comment_lines: int, source_lines: int) -> float:
    """Deterministically rounded comment-to-source ratio."""

    if source_lines <= 0:
        return 0.0
    return round(comment_lines / source_lines, _RATIO_PRECISION)


def _block_warning(
    filename: str,
    block_lines: int,
    policy: CodeCommentPolicy,
) -> EngineWarning | None:
    """Build the single PR-level ``oversized_comment_block`` warning, or ``None``."""

    if block_lines <= 0:
        return None
    if block_lines >= policy.fail.max_block_lines:
        tier, severity, threshold = (
            "fail",
            _SEVERITY_FAIL,
            policy.fail.max_block_lines,
        )
    elif block_lines >= policy.warn.max_block_lines:
        tier, severity, threshold = (
            "warn",
            _SEVERITY_WARN,
            policy.warn.max_block_lines,
        )
    else:
        return None
    return EngineWarning(
        code=WARN_CODE_OVERSIZED_BLOCK,
        severity=severity,
        message=(
            f"PR adds a {block_lines}-line consecutive comment block in "
            f"{filename}, exceeding the configured {tier} threshold of "
            f"{threshold} lines."
        ),
        evidence={
            "filename": filename,
            "comment_block_lines": block_lines,
            "threshold": threshold,
            "tier": tier,
        },
    )


def _total_warning(
    comment_lines: int,
    policy: CodeCommentPolicy,
) -> list[EngineWarning]:
    """Build the PR-level ``excessive_comment_lines`` warning, if any."""

    if comment_lines >= policy.fail.max_total_lines:
        tier, severity, threshold = (
            "fail",
            _SEVERITY_FAIL,
            policy.fail.max_total_lines,
        )
    elif comment_lines >= policy.warn.max_total_lines:
        tier, severity, threshold = (
            "warn",
            _SEVERITY_WARN,
            policy.warn.max_total_lines,
        )
    else:
        return []
    return [
        EngineWarning(
            code=WARN_CODE_EXCESSIVE_LINES,
            severity=severity,
            message=(
                f"PR adds {comment_lines} comment lines across eligible "
                f"source files, exceeding the configured {tier} threshold "
                f"of {threshold} lines."
            ),
            evidence={
                "comment_lines_added": comment_lines,
                "threshold": threshold,
                "tier": tier,
            },
        ),
    ]


def _ratio_warning(
    stats: CommentStats,
    policy: CodeCommentPolicy,
) -> list[EngineWarning]:
    """Build the ``comment_heavy_diff`` warning, honoring the sample-size guard.

    The ratio never triggers when ``source_lines_added`` is below
    ``policy.min_added_source_lines``; block and total-volume dimensions
    stay active on small diffs.
    """

    source_lines = stats.comment_lines_added + stats.code_lines_added
    if source_lines < policy.min_added_source_lines:
        return []
    if stats.comment_ratio >= policy.fail.max_comment_ratio:
        tier, severity, threshold = (
            "fail",
            _SEVERITY_FAIL,
            policy.fail.max_comment_ratio,
        )
    elif stats.comment_ratio >= policy.warn.max_comment_ratio:
        tier, severity, threshold = (
            "warn",
            _SEVERITY_WARN,
            policy.warn.max_comment_ratio,
        )
    else:
        return []
    return [
        EngineWarning(
            code=WARN_CODE_COMMENT_HEAVY,
            severity=severity,
            message=(
                f"PR adds {stats.comment_lines_added} comment lines across "
                f"{source_lines} added source lines (comment ratio "
                f"{stats.comment_ratio}), exceeding the configured {tier} "
                f"threshold of {threshold}."
            ),
            evidence={
                "comment_lines_added": stats.comment_lines_added,
                "source_lines_added": source_lines,
                "comment_ratio": stats.comment_ratio,
                "threshold": threshold,
                "tier": tier,
            },
        ),
    ]


__all__ = [
    "WARN_CODE_COMMENT_HEAVY",
    "WARN_CODE_EXCESSIVE_LINES",
    "WARN_CODE_OVERSIZED_BLOCK",
    "CommentAnalysis",
    "CommentStats",
    "analyze_added_comments",
]
