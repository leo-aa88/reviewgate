"""Unified-diff walker behind :mod:`reviewgate.core.code_comments`.

This module is private to ``reviewgate.core``. It decides which lines of a
:attr:`reviewgate.core.schemas.ChangedFile.patch` exist in the post-image,
when a hunk's lexical entry state is known well enough to tally, and how
added lines are counted. Language profiles and the line scanner live in
:mod:`reviewgate.core._comment_lex`.

Conservative-parsing contract (issue #143: false negatives are preferable
to noisy false positives):

* Only **added** patch lines are tallied. Deleted lines are dropped, not
  scanned: they are absent from the post-image, so surrounding added lines
  become adjacent in the resulting file and a deletion never terminates a
  comment block.
* Unchanged context lines are **scanned** so lexical state stays accurate
  (open ``/* */`` blocks, strings, heredocs), but are never tallied. They do
  terminate a block, because they survive in the post-image between two
  added lines.
* A hunk that does not start at new-file line 0 or 1 begins at an unknown
  lexical position, so it starts unestablished. Its own context lines can
  establish that position (see :data:`_CONTEXT_LINES_TO_ESTABLISH`); until
  they do, the hunk contributes nothing.

Pure: stdlib only, no I/O, no GitHub or LLM dependency (§4.1 boundary).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ._comment_lex import (
    _KIND_BLANK,
    _KIND_COMMENT,
    _PatchLine,
    _Profile,
    _ScanState,
    _scan_line,
    _state_is_clean,
)

# --- diff processing ---------------------------------------------------------


@dataclass(frozen=True)
class _FileTally:
    """Per-file added-line counts from one patch."""

    comment_lines: int
    code_lines: int
    largest_block: int


_HUNK_HEADER: Final[re.Pattern[str]] = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@"
)

_CONTEXT_LINES_TO_ESTABLISH: Final[int] = 2
"""Consecutive clean context lines needed to establish a mid-file hunk.

A hunk that does not start at new-file line 0 or 1 can begin inside a
string, block comment, or heredoc opened above Git's context window, so its
entry state starts unknown (issue #143: unknown syntax prefers false
negatives). A *single* context line proves nothing about where it began --
a line of docstring prose and a line of code are indistinguishable on their
own -- but a run of them that all scan clean, with no construct left open,
is positive evidence that the hunk starts at a normal code position.

Two is the smallest run that carries that evidence while keeping the known
failure case silent: a hunk whose only leading context line sits inside an
unterminated docstring stays unestablished and contributes nothing. The
residual risk is a hunk that begins two or more lines into a multi-line
string body; that is accepted in exchange for the heuristic actually firing
on edits to existing files, which is the case issue #143 exists to catch.
"""


def _hunk_entry_is_known(header: str) -> bool:
    """True when a hunk starts at the beginning of the new file.

    New-file line 0 or 1 is the only position where "normal" lexical state
    is known without any evidence, so it alone is established immediately.
    Every other start is established by :func:`_tally_patch` from the
    hunk's own context lines, or contributes nothing.
    """

    match = _HUNK_HEADER.match(header)
    if match is None:
        return False
    return int(match.group(1)) <= 1


def _iter_patch_lines(patch: str) -> list[_PatchLine]:
    """Classify unified-diff lines into added / context / gap.

    Added lines are the only ones that contribute to metrics. Context
    lines (space prefix) are post-image content: the lexer must see them to
    establish and carry lexical state, but they are never tallied. Deleted
    lines are dropped rather than marked as gaps -- they do not exist in the
    resulting file, so surrounding added lines become adjacent and a
    deletion must never terminate a comment block.

    Everything before the first ``@@`` of a file's diff section is
    **preamble** (``diff --git``, ``index``, ``--- a/...``, ``+++ b/...``,
    mode lines), never content. Headers are therefore detected by *position*
    rather than by re-sniffing each line's leading characters: inside a
    hunk an added ``++i`` is emitted as ``+++i`` and a deleted shell
    ``-- ) shift ;;`` as ``--- ) shift ;;``, so any content-shaped header
    test eventually collides with real code. ``parse_diff_right_side`` in
    ``scripts/_pr_review_llm.py`` hits the same collision and resolves it
    the same way.
    """

    extracted: list[_PatchLine] = []
    in_hunks = False
    for line in patch.splitlines():
        if not in_hunks:
            if line.startswith("@@"):
                in_hunks = True
            extracted.append(_PatchLine("gap", line))
            continue
        if line.startswith("+"):
            extracted.append(_PatchLine("added", line[1:]))
        elif line.startswith("-"):
            continue
        elif line.startswith(" "):
            extracted.append(_PatchLine("context", line[1:]))
        else:
            extracted.append(_PatchLine("gap", line))
    return extracted


def _tally_patch(patch: str, profile: _Profile) -> _FileTally:
    """Count added comment / code lines and the largest block in one patch.

    A hunk contributes only once its entry lexical state is established:
    either it starts at new-file line 0 or 1, or its own context lines have
    established a normal code position (see
    :data:`_CONTEXT_LINES_TO_ESTABLISH`). Until then its added lines are
    scanned for state but never tallied.
    """

    state = _ScanState()
    comment_lines = 0
    code_lines = 0
    current_block = 0
    largest_block = 0
    established = False
    clean_context = 0
    for item in _iter_patch_lines(patch):
        if item.kind == "gap":
            state = _ScanState()
            current_block = 0
            clean_context = 0
            if item.content.startswith("@@"):
                established = _hunk_entry_is_known(item.content)
            continue
        kind = _scan_line(item.content, state, profile)
        if item.kind == "context":
            # Context survives between added lines in the post-image, so it
            # always terminates a block; it is scanned but never tallied.
            current_block = 0
            if _state_is_clean(state):
                clean_context += 1
                if clean_context >= _CONTEXT_LINES_TO_ESTABLISH:
                    established = True
            else:
                clean_context = 0
            continue
        if not established:
            continue
        if kind == _KIND_BLANK:
            current_block = 0
        elif kind == _KIND_COMMENT:
            comment_lines += 1
            current_block += 1
            if current_block > largest_block:
                largest_block = current_block
        else:
            code_lines += 1
            current_block = 0
    return _FileTally(
        comment_lines=comment_lines,
        code_lines=code_lines,
        largest_block=largest_block,
    )


def _count_added_source_lines(patch: str) -> int:
    """Count added non-blank lines in a file whose comment syntax is unmodeled.

    Such a file cannot contribute comment lines, but its added lines are
    real source lines. Dropping them from the ratio denominator would
    inflate ``comment_ratio`` over the files the scanner *can* read and
    manufacture the false positive issue #143 forbids, so they are counted
    as source (denominator) only, never as commentary (numerator).
    """

    return sum(
        1
        for item in _iter_patch_lines(patch)
        if item.kind == "added" and item.content.strip()
    )


__all__ = [
    "_CONTEXT_LINES_TO_ESTABLISH",
    "_count_added_source_lines",
    "_hunk_entry_is_known",
    "_iter_patch_lines",
    "_tally_patch",
]
