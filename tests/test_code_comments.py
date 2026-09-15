"""Tests for :mod:`reviewgate.core.code_comments` (issue #143).

Locks the deterministic excessive-comment-verbosity heuristic end to end:

* **Added-lines-only metrics**: deleted lines are dropped; context lines
  are scanned for lexical state but never tallied, so a PR is never
  penalised for historical comments it did not touch.
* **Conservative classification**: string content (including JS/Go
  backtick strings and shell quotes/heredocs), docstrings, trailing
  comments, mid-file hunks with unknown entry state, and unsupported
  languages (including unmodeled C-family / JSX suffixes) are never
  counted; false negatives are preferred over false positives.
* **One warning per dimension**: ``oversized_comment_block`` is a single
  PR-level warning for the maximum block (filename in evidence), matching
  :func:`reviewgate.core.size.size_warnings`.
* **Threshold ladder**: warn/fail thresholds are inclusive lower bounds,
  the same convention as :func:`reviewgate.core.size.size_warnings`, and
  the ratio dimension honors the minimum sample-size guard.
* **File eligibility**: reuses the categorizer's ``human_authored`` /
  ``source`` verdict; docs, generated, vendored, minified, snapshot,
  lockfile, and manifest files are excluded.
* **Integration**: warnings flow into the ordinary §10.13 aggregation and
  stats keys appear only while the policy is enabled.

Pure: these tests never touch the filesystem beyond pytest's own imports.
"""

from __future__ import annotations

from typing import Final

import pytest

from reviewgate.core.aggregate import baseline_reviewability
from reviewgate.core.code_comments import (
    WARN_CODE_COMMENT_HEAVY,
    WARN_CODE_EXCESSIVE_LINES,
    WARN_CODE_OVERSIZED_BLOCK,
    CommentAnalysis,
    analyze_added_comments,
)
from reviewgate.core.config import (
    CONFIG_WARNING_CODE,
    CodeCommentPolicy,
    ReviewGateConfig,
    load_config,
)
from reviewgate.core.engine import analyze
from reviewgate.core.schemas import ChangedFile, EngineInput, FileCategoryRow, PRRecord

# --- helpers -----------------------------------------------------------------


def _changed(filename: str, patch: str | None, *, changes: int = 0) -> ChangedFile:
    return ChangedFile(
        filename=filename,
        status="modified",
        additions=changes,
        deletions=0,
        changes=changes,
        patch=patch,
    )


def _row(
    filename: str,
    *,
    categories: tuple[str, ...] = ("source",),
    human_authored: bool = True,
) -> FileCategoryRow:
    return FileCategoryRow(
        filename=filename,
        categories=list(categories),  # type: ignore[arg-type]
        risky=False,
        human_authored=human_authored,
        changes=0,
    )


def _diff(*added_lines: str) -> str:
    """Wrap raw post-``+`` line contents into a minimal unified diff."""

    lines = ["@@ -1,1 +1,%d @@" % len(added_lines)]
    lines.extend(f"+{line}" for line in added_lines)
    return "\n".join(lines)


def _policy(**overrides: object) -> CodeCommentPolicy:
    """Build a CodeCommentPolicy, optionally overriding nested thresholds.

    Warn overrides that exceed the fail defaults automatically raise the
    matching fail threshold too, so single-dimension tests do not trip the
    ``warn <= fail`` cross-field validation.
    """

    warn: dict[str, object] = {
        "max_block_lines": 10,
        "max_total_lines": 60,
        "max_comment_ratio": 0.45,
    }
    fail: dict[str, object] = {
        "max_block_lines": 25,
        "max_total_lines": 150,
        "max_comment_ratio": 0.70,
    }
    kwargs: dict[str, object] = {"enabled": True, "min_added_source_lines": 20}
    for key, value in overrides.items():
        if key.startswith("warn_"):
            warn[key[len("warn_") :]] = value
        elif key.startswith("fail_"):
            fail[key[len("fail_") :]] = value
        else:
            kwargs[key] = value
    for name, warn_value in warn.items():
        if name not in fail or fail[name] < warn_value:
            fail[name] = warn_value
    return CodeCommentPolicy(
        warn=warn,  # type: ignore[arg-type]
        fail=fail,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def _analyze(
    entries: list[tuple[str, str | None, FileCategoryRow]],
    policy: CodeCommentPolicy | None = None,
) -> CommentAnalysis:
    files = [_changed(name, patch) for name, patch, _ in entries]
    rows = [row for _, _, row in entries]
    return analyze_added_comments(files, rows, policy or _policy())


# --- added-lines-only extraction ----------------------------------------------


class TestAddedLinesOnly:
    def test_deleted_and_context_lines_are_ignored(self) -> None:
        """Deleted comment lines never count; context comments are scanned
        for lexical state but not tallied. The feature measures the PR's
        added lines, nothing else."""

        patch = "\n".join(
            [
                "@@ -1,6 +1,3 @@",
                "-# deleted comment one",
                "-# deleted comment two",
                " # context comment",
                "+# added comment",
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 1
        assert result.stats.largest_comment_block_lines == 1
        assert result.warnings == []

    def test_hunk_and_metadata_lines_are_ignored(self) -> None:
        patch = "\n".join(
            [
                "diff --git a/app.py b/app.py",
                "--- a/app.py",
                "+++ b/app.py",
                "@@ -1,2 +1,2 @@",
                "+# one",
                "\\ No newline at end of file",
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 1

    def test_added_increment_operator_is_code_not_a_file_header(self) -> None:
        """An added ``++i`` is rendered ``+++i`` and must stay a code line."""

        patch = "\n".join(
            [
                "diff --git a/counter.js b/counter.js",
                "--- a/counter.js",
                "+++ b/counter.js",
                "@@ -1,1 +1,3 @@",
                " let i = 0;",
                "+// bump the counter",
                "+++i;",
            ]
        )
        result = _analyze([("src/counter.js", patch, _row("src/counter.js"))])
        assert result.stats.code_lines_added == 1
        assert result.stats.comment_lines_added == 1

    def test_deleted_decrement_operator_does_not_split_a_comment_run(
        self,
    ) -> None:
        """A deleted ``--x`` is rendered ``---x`` and must not reset a run."""

        patch = "\n".join(
            [
                "diff --git a/main.go b/main.go",
                "--- a/main.go",
                "+++ b/main.go",
                "@@ -1,1 +1,4 @@",
                "+// one",
                "+// two",
                "---x;",
                "+// three",
            ]
        )
        result = _analyze([("src/main.go", patch, _row("src/main.go"))])
        assert result.stats.comment_lines_added == 3
        assert result.stats.largest_comment_block_lines == 3

    def test_patch_with_only_deletions_yields_zero_stats(self) -> None:
        patch = "@@ -1,3 +1,0 @@\n-# old\n-# block\n-# gone"
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.model_dump() == {
            "comment_lines_added": 0,
            "code_lines_added": 0,
            "largest_comment_block_lines": 0,
            "comment_ratio": 0.0,
        }

    def test_missing_patch_is_skipped_without_error(self) -> None:
        result = _analyze([("src/app.py", None, _row("src/app.py"))])
        assert result.warnings == []
        assert result.stats.comment_lines_added == 0


# --- conservative classification ------------------------------------------------


class TestConservativeClassification:
    def test_url_containing_slashes_is_code(self) -> None:
        patch = _diff('url := "https://example.com//path"')
        result = _analyze([("src/main.go", patch, _row("src/main.go"))])
        assert result.stats.comment_lines_added == 0

    def test_hash_inside_python_string_is_code(self) -> None:
        patch = _diff('pattern = "#[a-z]+"')
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 0

    def test_slashes_inside_python_string_are_code(self) -> None:
        patch = _diff('endpoint = "https://api.example.com//v2"')
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 0

    def test_marker_mid_line_is_not_a_comment(self) -> None:
        patch = _diff("value = hash  # trailing comments are not counted")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 0
        assert result.stats.code_lines_added == 1

    def test_trailing_c_style_comment_is_not_counted(self) -> None:
        patch = _diff("retry() // upstream occasionally returns 503")
        result = _analyze([("src/svc.go", patch, _row("src/svc.go"))])
        assert result.stats.comment_lines_added == 0
        assert result.stats.code_lines_added == 1

    def test_python_docstrings_are_never_comments(self) -> None:
        """Docstrings are string literals and may be runtime data (issue #143)."""

        patch = _diff(
            "def f():",
            '    """Summary line.',
            "    Body mentioning # hash and // slashes.",
            '    """',
            "    return 1",
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 0
        assert result.stats.code_lines_added == 5
        assert result.stats.largest_comment_block_lines == 0

    def test_hash_line_inside_docstring_is_not_a_comment(self) -> None:
        patch = _diff(
            '"""',
            "# looks like a comment but is string content",
            '"""',
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 0

    def test_shebang_counts_as_code_not_commentary(self) -> None:
        patch = _diff("#!/usr/bin/env python", "# real comment")
        result = _analyze([("scripts/tool.py", patch, _row("scripts/tool.py"))])
        assert result.stats.comment_lines_added == 1

    def test_unsupported_language_is_skipped(self) -> None:
        patch = _diff("# ruby comment", "# another")
        result = _analyze([("app/rb/task.rb", patch, _row("app/rb/task.rb"))])
        assert result.stats.comment_lines_added == 0
        assert result.warnings == []

    def test_blank_lines_count_toward_neither_bucket(self) -> None:
        patch = _diff("# comment", "", "code()")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 1
        assert result.stats.code_lines_added == 1


# --- block-comment and block-size semantics --------------------------------------


class TestBlockSemantics:
    def test_c_style_block_comment_lines_form_a_block(self) -> None:
        patch = _diff("/* opening", " * continued", " */", "code();")
        result = _analyze([("src/main.go", patch, _row("src/main.go"))])
        assert result.stats.largest_comment_block_lines == 3

    def test_multiple_separate_blocks_report_largest(self) -> None:
        patch = _diff("# a", "# b", "# c", "x = 1", "# d", "# e")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 5
        assert result.stats.largest_comment_block_lines == 3

    def test_blank_line_terminates_a_block(self) -> None:
        patch = _diff("# a", "# b", "", "# c", "# d")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 4
        assert result.stats.largest_comment_block_lines == 2

    def test_code_line_terminates_a_block(self) -> None:
        patch = _diff("# a", "x = 1", "# b")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.largest_comment_block_lines == 1

    def test_context_line_terminates_a_block(self) -> None:
        """A run interrupted by a context line is two smaller blocks: the
        comment lines are not adjacent in the file, so treating them as one
        large block would overstate the introduced commentary."""

        patch = "\n".join(
            [
                "@@ -1,5 +1,6 @@",
                "+# a",
                "+# b",
                " existing = line",
                "+# c",
                "+# d",
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 4
        assert result.stats.largest_comment_block_lines == 2


# --- context-aware lexical state (PR #144 review) --------------------------------


class TestContextAwareLexicalState:
    def test_context_closer_ends_block_comment_before_added_code(self) -> None:
        """Reviewer reproduction: added `/*`, unchanged `*/`, then added code.
        Only the added opener is commentary; carrying `in_block_comment` past
        the context closer would count every `int` line as a comment."""

        patch = "\n".join(
            [
                "@@ -1,3 +1,15 @@",
                "+/*",
                " existing text",
                " */",
                *[f"+int value_{i} = {i};" for i in range(12)],
            ]
        )
        result = _analyze([("src/main.go", patch, _row("src/main.go"))])
        assert result.stats.comment_lines_added == 1
        assert result.stats.code_lines_added == 12
        assert result.stats.largest_comment_block_lines == 1
        assert result.warnings == []

    def test_hash_inside_context_opened_docstring_is_not_a_comment(self) -> None:
        """Inverse: a triple-quoted string opened by context must suppress
        added `#` lines, matching the documented docstring contract."""

        patch = "\n".join(
            [
                "@@ -1,3 +1,4 @@",
                ' """',
                "+# looks like a comment but is string content",
                " body",
                ' """',
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 0
        assert result.warnings == []

    def test_context_opener_makes_added_block_body_comments(self) -> None:
        """Lines added inside an already-open `/*` *are* new commentary."""

        patch = "\n".join(
            [
                "@@ -1,3 +1,5 @@",
                " /*",
                "+ * added inside existing block",
                "+ * another",
                " */",
                "+int x = 0;",
            ]
        )
        result = _analyze([("src/main.go", patch, _row("src/main.go"))])
        assert result.stats.comment_lines_added == 2
        assert result.stats.code_lines_added == 1

    def test_mid_file_hunk_does_not_assume_normal_state(self) -> None:
        """Reviewer reproduction: a hunk whose opener sits above Git's
        context window must not treat added `#` lines as comments."""

        patch = "\n".join(
            [
                "@@ -100,3 +100,13 @@",
                " existing docstring text",
                "+# payload 0",
                *[f"+# payload {i}" for i in range(1, 10)],
                " more docstring text",
                ' """',
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 0
        assert result.warnings == []

    def test_file_start_hunk_is_still_analyzed(self) -> None:
        """New-file line 1 is known-normal; skipping every hunk would
        disable the heuristic on ordinary patches."""

        patch = "\n".join(
            [
                "@@ -1,1 +1,3 @@",
                "+# real comment",
                "+x = 1",
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 1
        assert result.stats.code_lines_added == 1

    def test_mid_file_hunk_is_skipped_not_reset_to_normal(self) -> None:
        """A later hunk cannot inherit 'normal' from a default ScanState.
        Hunk 1 starts at line 1 and is counted; hunk 2 starts at line 20
        and must not contribute metrics."""

        patch = "\n".join(
            [
                "@@ -1,1 +1,1 @@",
                "+/* opened in hunk 1",
                "@@ -20,1 +20,1 @@",
                "+int x = 0;",
            ]
        )
        result = _analyze([("src/main.go", patch, _row("src/main.go"))])
        assert result.stats.comment_lines_added == 1
        assert result.stats.code_lines_added == 0


# --- hunk entry established from context (PR #144 review round 4) ----------------


class TestHunkEntryEstablishedFromContext:
    def test_mid_file_hunk_with_code_context_is_analyzed(self) -> None:
        """Reviewer reproduction: an ordinary `git diff -U3` hunk on an
        existing file (new-file start > 1) adding a 12-line block. Skipping
        every mid-file hunk made the heuristic a no-op on the exact case
        issue #143 exists to catch."""

        patch = "\n".join(
            [
                "@@ -80,6 +80,23 @@ def handle():",
                "     existing = 1",
                "     other = 2",
                "     third = 3",
                *[f"+# comment line {i}" for i in range(12)],
                "     tail = 4",
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 12
        assert result.stats.largest_comment_block_lines == 12
        assert [w.code for w in result.warnings] == [WARN_CODE_OVERSIZED_BLOCK]
        assert result.warnings[0].severity == "medium"

    def test_single_context_line_does_not_establish_entry_state(self) -> None:
        """One context line proves nothing about where the hunk began: a
        line of docstring prose and a line of code are indistinguishable on
        their own, so this hunk must stay silent."""

        patch = "\n".join(
            [
                "@@ -50,1 +50,3 @@",
                " existing docstring text",
                "+# payload 0",
                "+# payload 1",
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 0
        assert result.warnings == []

    def test_two_clean_context_lines_establish_entry_state(self) -> None:
        """A run of context lines that all scan clean is positive evidence
        that the hunk starts at a normal code position."""

        patch = "\n".join(
            [
                "@@ -50,2 +50,4 @@",
                " value = 1",
                " other = 2",
                "+# real comment",
                "+total = 3",
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 1
        assert result.stats.code_lines_added == 1

    def test_open_construct_context_never_establishes_entry_state(self) -> None:
        """Three context lines are still no evidence when every one of them
        sits inside an open heredoc, so the hunk contributes nothing at
        all. Contrast with the two clean context lines above, which do
        establish state on the same hunk shape."""

        patch = "\n".join(
            [
                "@@ -10,5 +10,7 @@",
                " cat <<EOF",
                " body one",
                " body two",
                "+# inside the heredoc body",
                "+another",
                " EOF",
            ]
        )
        result = _analyze([("scripts/run.sh", patch, _row("scripts/run.sh"))])
        assert result.stats.comment_lines_added == 0
        assert result.stats.code_lines_added == 0
        assert result.warnings == []


# --- file header detection by position (PR #144 review round 4) -------------------


class TestDiffHeaderDetection:
    def test_deleted_shell_case_arm_does_not_split_a_block(self) -> None:
        """Reviewer reproduction: a deleted `-- ) shift ;;` is emitted as
        `--- ) shift ;;`. Content-shaped header matching reclassified it as
        a gap, which wiped lexer state and split the run at 3 instead of 4."""

        patch = "\n".join(
            [
                "@@ -1,3 +1,8 @@",
                "+# one",
                "+# two",
                "+# three",
                "--- ) shift ;;",
                "+# four",
            ]
        )
        result = _analyze([("scripts/run.sh", patch, _row("scripts/run.sh"))])
        assert result.stats.comment_lines_added == 4
        assert result.stats.largest_comment_block_lines == 4

    def test_added_increment_operator_counts_as_code(self) -> None:
        """An added `++i` is emitted as `+++i` and must be tallied as code,
        not swallowed as a file header."""

        patch = "\n".join(
            [
                "@@ -1,2 +1,6 @@",
                "+// a",
                "+++i;",
                "+// b",
                "+let x = 1;",
            ]
        )
        result = _analyze([("src/a.js", patch, _row("src/a.js"))])
        assert result.stats.comment_lines_added == 2
        assert result.stats.code_lines_added == 2
        assert result.stats.largest_comment_block_lines == 1

    def test_preamble_before_first_hunk_is_never_content(self) -> None:
        """`--- a/...` and `+++ b/...` precede the first `@@`, so position
        alone classifies them as preamble rather than as added or deleted
        source lines."""

        patch = "\n".join(
            [
                "--- a/src/app.py",
                "+++ b/src/app.py",
                "@@ -1,1 +1,4 @@",
                "+# one",
                "+# two",
                "+x = 1",
            ]
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 2
        assert result.stats.code_lines_added == 1
        assert result.stats.largest_comment_block_lines == 2


# --- ratio denominator covers unmodeled languages (PR #144 review round 4) --------


class TestRatioDenominator:
    def test_unmodeled_source_file_stays_in_the_denominator(self) -> None:
        """Reviewer reproduction: 9 comment + 11 code lines in Python plus
        100 code lines in Java is 9/120 ~= 0.075 at PR level. Dropping the
        unmodeled file reported 0.45 and fired `comment_heavy_diff`."""

        python_patch = "\n".join(
            ["@@ -1,1 +1,21 @@"]
            + [f"+# c{i}" for i in range(9)]
            + [f"+code_{i} = {i}" for i in range(11)]
        )
        java_patch = "\n".join(
            ["@@ -1,1 +1,101 @@"] + [f"+int v{i} = {i};" for i in range(100)]
        )
        result = _analyze(
            [
                ("src/a.py", python_patch, _row("src/a.py")),
                ("src/B.java", java_patch, _row("src/B.java")),
            ]
        )
        assert result.stats.comment_lines_added == 9
        assert result.stats.code_lines_added == 111
        assert result.stats.comment_ratio == round(9 / 120, 4)
        assert result.warnings == []

    def test_unmodeled_file_never_contributes_comment_lines(self) -> None:
        """Comment syntax is not modeled for the language, so its `#` or
        `//` lines may join the denominator but never the numerator."""

        patch = "\n".join(["@@ -1,1 +1,3 @@", "+// not classified", "+int x = 1;"])
        result = _analyze([("src/B.java", patch, _row("src/B.java"))])
        assert result.stats.comment_lines_added == 0
        assert result.stats.code_lines_added == 2

    def test_ineligible_file_stays_out_of_the_denominator(self) -> None:
        """A generated file is out of scope entirely: it must not be
        counted as source just because its language is unmodeled."""

        patch = "\n".join(["@@ -1,1 +1,3 @@", "+int x = 1;", "+int y = 2;"])
        result = _analyze(
            [("src/gen.java", patch, _row("src/gen.java", human_authored=False))]
        )
        assert result.stats.code_lines_added == 0
        assert result.stats.comment_lines_added == 0


# --- multiline strings / heredocs (PR #144 review) -------------------------------


class TestMultilineStrings:
    def test_js_template_literal_body_is_not_commentary(self) -> None:
        """Reviewer reproduction: unclosed backtick must carry state so
        `// payload` lines inside a template literal are never comments."""

        patch = _diff(
            "const banner = `",
            *["// payload %d" % i for i in range(12)],
            "`;",
        )
        result = _analyze([("src/app.js", patch, _row("src/app.js"))])
        assert result.stats.comment_lines_added == 0
        assert result.warnings == []

    def test_go_raw_string_body_is_not_commentary(self) -> None:
        patch = _diff(
            "banner := `",
            *["// payload %d" % i for i in range(12)],
            "`",
        )
        result = _analyze([("src/main.go", patch, _row("src/main.go"))])
        assert result.stats.comment_lines_added == 0
        assert result.warnings == []

    def test_ts_template_literal_body_is_not_commentary(self) -> None:
        patch = _diff("const banner = `", "// payload", "`;", "export {};")
        result = _analyze([("src/app.ts", patch, _row("src/app.ts"))])
        assert result.stats.comment_lines_added == 0

    def test_shell_heredoc_hash_lines_are_not_comments(self) -> None:
        patch = _diff("cat <<EOF", "# not a comment", "payload", "EOF", "echo done")
        result = _analyze([("scripts/run.sh", patch, _row("scripts/run.sh"))])
        assert result.stats.comment_lines_added == 0
        assert result.stats.code_lines_added == 5

    def test_shell_quoted_heredoc_hash_lines_are_not_comments(self) -> None:
        patch = _diff("python - <<'PY'", "# still string content", "print(1)", "PY")
        result = _analyze([("scripts/run.sh", patch, _row("scripts/run.sh"))])
        assert result.stats.comment_lines_added == 0

    def test_shell_dash_heredoc_strips_tabs_on_closer(self) -> None:
        patch = _diff("cat <<-EOF", "\t# body", "\tEOF", "echo done")
        result = _analyze([("scripts/run.sh", patch, _row("scripts/run.sh"))])
        assert result.stats.comment_lines_added == 0
        assert result.stats.code_lines_added == 4

    def test_shell_single_quoted_multiline_hash_is_not_a_comment(self) -> None:
        patch = _diff("x='", "# not a comment", "foo'", "echo hi")
        result = _analyze([("scripts/run.sh", patch, _row("scripts/run.sh"))])
        assert result.stats.comment_lines_added == 0
        assert result.stats.code_lines_added == 4

    def test_shell_double_quoted_multiline_hash_is_not_a_comment(self) -> None:
        patch = _diff('x="', "# not a comment", 'foo"', "echo hi")
        result = _analyze([("scripts/run.sh", patch, _row("scripts/run.sh"))])
        assert result.stats.comment_lines_added == 0

    def test_python_backslash_continued_string_is_not_a_comment(self) -> None:
        patch = _diff('x = "hello \\', "# not a comment", 'world"')
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 0

    def test_comment_after_closed_template_literal_still_counts(self) -> None:
        patch = _diff("const banner = `", "payload", "`;", "// real comment")
        result = _analyze([("src/app.js", patch, _row("src/app.js"))])
        assert result.stats.comment_lines_added == 1
        assert result.stats.code_lines_added == 3

    def test_rust_raw_string_is_skipped_not_miscounted(self) -> None:
        """Rust is advertised only when its raw-string state exists. Until
        then the suffix is unsupported, so `r#\" // \"#` cannot warn."""

        patch = _diff(
            'let payload = r#"',
            *["// payload %d" % i for i in range(12)],
            '"#;',
        )
        result = _analyze([("src/main.rs", patch, _row("src/main.rs"))])
        assert result.stats.comment_lines_added == 0
        assert result.warnings == []

    def test_java_text_block_is_skipped_not_miscounted(self) -> None:
        patch = _diff(
            'String payload = """',
            *["// payload %d" % i for i in range(12)],
            '""";',
        )
        result = _analyze([("src/Main.java", patch, _row("src/Main.java"))])
        assert result.stats.comment_lines_added == 0
        assert result.warnings == []

    def test_jsx_is_skipped_until_jsx_text_is_modeled(self) -> None:
        patch = _diff("export const n = 1;", "// not analyzed")
        result = _analyze([("src/app.tsx", patch, _row("src/app.tsx"))])
        assert result.stats.comment_lines_added == 0

    @pytest.mark.parametrize(
        "filename",
        ["src/main.c", "src/main.cpp", "src/Main.cs", "src/app.jsx"],
    )
    def test_remaining_unmodeled_suffixes_are_skipped(self, filename: str) -> None:
        """C, C++, C#, and JSX were advertised with the C-family profile
        but their raw strings / JSX text are not modeled, so they must
        not emit comment warnings (issue #143)."""

        patch = _diff(*["// payload %d" % i for i in range(12)])
        result = _analyze([(filename, patch, _row(filename))])
        assert result.stats.comment_lines_added == 0
        assert result.warnings == []


# --- threshold ladder -------------------------------------------------------------


class TestThresholdLadder:
    def test_ordinary_comments_below_thresholds_emit_nothing(self) -> None:
        patch = _diff(*["# line %d" % i for i in range(9)], "x = 1")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.warnings == []

    def test_block_exactly_at_warn_threshold_triggers_medium(self) -> None:
        """Thresholds are inclusive lower bounds, matching size_warnings."""

        patch = _diff(*["# line %d" % i for i in range(10)])
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert [(w.code, w.severity) for w in result.warnings] == [
            (WARN_CODE_OVERSIZED_BLOCK, "medium")
        ]

    def test_block_one_above_warn_stays_medium(self) -> None:
        patch = _diff(*["# line %d" % i for i in range(11)])
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert [(w.code, w.severity) for w in result.warnings] == [
            (WARN_CODE_OVERSIZED_BLOCK, "medium")
        ]

    def test_block_at_fail_threshold_triggers_high(self) -> None:
        patch = _diff(
            *["# line %d" % i for i in range(25)],
            *["x = %d" % i for i in range(60)],
        )
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert [(w.code, w.severity) for w in result.warnings] == [
            (WARN_CODE_OVERSIZED_BLOCK, "high")
        ]

    def test_total_lines_at_warn_and_fail_boundaries(self) -> None:
        def entries(count: int) -> list[tuple[str, str | None, FileCategoryRow]]:
            # Interleave filler code lines so no block exceeds one line and
            # the ratio dimension never fires: only total volume moves.
            entries_ = []
            per_file = count // 2
            for idx in range(2):
                lines: list[str] = []
                for i in range(per_file):
                    lines.append("# c%d-%d" % (idx, i))
                    lines.append("x%d = %d" % (idx, i))
                lines.extend("y%d = %d" % (idx, i) for i in range(200))
                patch = _diff(*lines)
                entries_.append((f"src/app{idx}.py", patch, _row(f"src/app{idx}.py")))
            return entries_

        warn_result = _analyze(entries(60))
        assert [(w.code, w.severity) for w in warn_result.warnings] == [
            (WARN_CODE_EXCESSIVE_LINES, "medium")
        ]
        fail_result = _analyze(entries(150))
        assert [(w.code, w.severity) for w in fail_result.warnings] == [
            (WARN_CODE_EXCESSIVE_LINES, "high")
        ]

    def test_total_lines_below_warn_emits_nothing(self) -> None:
        lines: list[str] = []
        for i in range(59):
            lines.append("# c%d" % i)
            lines.append("x = %d" % i)
        lines.extend("y = %d" % i for i in range(200))
        patch = _diff(*lines)
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.warnings == []

    def test_ratio_at_warn_boundary_triggers(self) -> None:
        policy = _policy(
            warn_max_comment_ratio=0.5,
            fail_max_comment_ratio=0.8,
            min_added_source_lines=10,
            warn_max_total_lines=1000,
            fail_max_total_lines=1000,
        )
        # 5 comment lines / 10 non-blank lines = 0.5 exactly.
        patch = _diff("# a", "# b", "# c", "# d", "# e", *["x = %d" % i for i in range(5)])
        result = _analyze([("src/app.py", patch, _row("src/app.py"))], policy)
        assert [(w.code, w.severity) for w in result.warnings] == [
            (WARN_CODE_COMMENT_HEAVY, "medium")
        ]

    def test_ratio_below_boundary_is_silent(self) -> None:
        policy = _policy(
            warn_max_comment_ratio=0.5,
            min_added_source_lines=10,
            warn_max_total_lines=1000,
        )
        patch = _diff("# a", "# b", "# c", "# d", *["x = %d" % i for i in range(6)])
        result = _analyze([("src/app.py", patch, _row("src/app.py"))], policy)
        assert result.warnings == []

    def test_ratio_suppressed_below_minimum_sample_size(self) -> None:
        """The tiny-diff guard: 2 comments over 3 lines is a 0.67 ratio but
        must stay silent under ``min_added_source_lines``."""

        policy = _policy(min_added_source_lines=20, warn_max_total_lines=1000)
        patch = _diff("# workaround for upstream bug", "# see issue", "foo()")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))], policy)
        assert result.warnings == []
        assert result.stats.comment_ratio == round(2 / 3, 4)

    def test_sample_size_guard_does_not_block_block_dimension(self) -> None:
        """An enormous block is meaningful even in a small diff (issue #143)."""

        policy = _policy(min_added_source_lines=20, warn_max_total_lines=1000)
        patch = _diff(*["# line %d" % i for i in range(11)], "foo()")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))], policy)
        assert [w.code for w in result.warnings] == [WARN_CODE_OVERSIZED_BLOCK]

    def test_sample_size_guard_does_not_block_total_dimension(self) -> None:
        policy = _policy(
            min_added_source_lines=1000,
            warn_max_total_lines=3,
            fail_max_total_lines=1000,
        )
        patch = _diff("# a", "# b", "# c")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))], policy)
        assert [w.code for w in result.warnings] == [WARN_CODE_EXCESSIVE_LINES]

    def test_custom_thresholds_override_defaults(self) -> None:
        policy = _policy(
            warn_max_block_lines=2,
            fail_max_block_lines=3,
            warn_max_total_lines=1000,
        )
        patch = _diff("# a", "# b", "code()")
        result = _analyze([("src/app.py", patch, _row("src/app.py"))], policy)
        assert [(w.code, w.severity) for w in result.warnings] == [
            (WARN_CODE_OVERSIZED_BLOCK, "medium")
        ]

    def test_one_block_warning_for_pr_wide_maximum(self) -> None:
        """One code per dimension: two oversized files emit a single warning
        for the PR-wide maximum (the larger block), not one vote per file."""

        patch_a = _diff(*["# a%d" % i for i in range(12)], *["x = %d" % i for i in range(100)])
        patch_b = _diff(*["# b%d" % i for i in range(30)], *["x = %d" % i for i in range(100)])
        result = _analyze(
            [
                ("src/a.py", patch_a, _row("src/a.py")),
                ("src/b.py", patch_b, _row("src/b.py")),
            ]
        )
        assert [w.code for w in result.warnings] == [WARN_CODE_OVERSIZED_BLOCK]
        assert result.warnings[0].evidence["filename"] == "src/b.py"
        assert result.warnings[0].evidence["comment_block_lines"] == 30
        assert result.warnings[0].severity == "high"
        assert result.stats.largest_comment_block_lines == 30

    def test_split_warn_tier_blocks_do_not_escalate_verdict(self) -> None:
        """Reviewer reproduction: two 10-line blocks + 50 code lines each
        must emit one medium warning (PASS), not two mediums (WARN)."""

        entries = []
        for name in ("src/a.py", "src/b.py"):
            patch = _diff(
                *["# %s-%d" % (name, i) for i in range(10)],
                *["x = %d" % i for i in range(50)],
            )
            entries.append((name, patch, _row(name)))
        result = _analyze(entries)
        assert [(w.code, w.severity) for w in result.warnings] == [
            (WARN_CODE_OVERSIZED_BLOCK, "medium")
        ]
        assert result.warnings[0].evidence["filename"] == "src/a.py"
        assert baseline_reviewability(result.warnings) == "PASS"

    def test_two_fail_tier_blocks_do_not_cast_two_high_votes(self) -> None:
        """Two fail-tier files must not supply two high-severity votes
        (which would turn WARN into FAIL). Equal sizes pick the first file."""

        entries = []
        for name in ("src/a.py", "src/b.py"):
            patch = _diff(
                *["# %s-%d" % (name, i) for i in range(25)],
                *["x = %d" % i for i in range(60)],
            )
            entries.append((name, patch, _row(name)))
        result = _analyze(entries)
        assert [(w.code, w.severity) for w in result.warnings] == [
            (WARN_CODE_OVERSIZED_BLOCK, "high")
        ]
        assert result.warnings[0].evidence["filename"] == "src/a.py"
        assert baseline_reviewability(result.warnings) == "WARN"


# --- warning shape and determinism -------------------------------------------------


class TestWarningShape:
    def test_block_warning_evidence_is_complete(self) -> None:
        patch = _diff(*["// line %d" % i for i in range(17)])
        result = _analyze([("src/auth/session.go", patch, _row("src/auth/session.go"))])
        warning = result.warnings[0]
        assert warning.code == WARN_CODE_OVERSIZED_BLOCK
        assert warning.severity == "medium"
        assert warning.evidence == {
            "filename": "src/auth/session.go",
            "comment_block_lines": 17,
            "threshold": 10,
            "tier": "warn",
        }
        assert "src/auth/session.go" in warning.message
        assert "17" in warning.message

    def test_ratio_warning_evidence_is_complete(self) -> None:
        policy = _policy(
            warn_max_comment_ratio=0.45,
            warn_max_total_lines=1000,
            warn_max_block_lines=100,
            min_added_source_lines=10,
        )
        patch = _diff(*["# c%d" % i for i in range(91)], *["x = %d" % i for i in range(81)])
        result = _analyze([("src/app.py", patch, _row("src/app.py"))], policy)
        warning = result.warnings[0]
        assert warning.code == WARN_CODE_COMMENT_HEAVY
        assert warning.severity == "medium"
        assert warning.evidence == {
            "comment_lines_added": 91,
            "source_lines_added": 172,
            "comment_ratio": round(91 / 172, 4),
            "threshold": 0.45,
            "tier": "warn",
        }

    def test_analysis_is_deterministic_across_runs(self) -> None:
        patch = _diff(
            *["# c%d" % i for i in range(12)],
            "x = 1",
            "/* block",
            " * lines",
            " */",
        )
        entries = [("src/app.py", patch, _row("src/app.py"))]
        first = _analyze(entries)
        second = _analyze(entries)
        assert first.stats.model_dump() == second.stats.model_dump()
        assert [w.model_dump() for w in first.warnings] == [
            w.model_dump() for w in second.warnings
        ]


# --- file eligibility ----------------------------------------------------------------


class TestFileEligibility:
    @pytest.mark.parametrize(
        ("filename", "categories", "human_authored"),
        [
            ("docs/guide.py", ("docs", "source"), True),  # docs-labelled
            ("src/generated/models.py", ("generated", "source"), False),
            ("vendor/tools/main.go", ("vendored", "source"), False),
            ("public/app.min.js", ("minified", "source"), False),
            ("tests/__snapshots__/x.ts", ("snapshot", "source"), False),
            ("poetry.lock", ("lockfile",), True),
            ("package.json", ("dependency",), True),
            ("README.md", ("docs",), True),
            ("assets/logo.py", ("asset",), True),  # asset-labelled
            ("src/app.py", ("unknown",), True),  # not categorised source
        ],
    )
    def test_ineligible_files_are_skipped(
        self,
        filename: str,
        categories: tuple[str, ...],
        human_authored: bool,
    ) -> None:
        patch = _diff(*["# comment %d" % i for i in range(30)])
        result = _analyze(
            [
                (
                    filename,
                    patch,
                    _row(filename, categories=categories, human_authored=human_authored),
                )
            ]
        )
        assert result.warnings == []
        assert result.stats.comment_lines_added == 0

    def test_eligible_human_authored_source_is_analyzed(self) -> None:
        patch = _diff(*["# comment %d" % i for i in range(30)])
        result = _analyze([("src/app.py", patch, _row("src/app.py"))])
        assert result.stats.comment_lines_added == 30

    def test_test_files_are_eligible_source(self) -> None:
        patch = _diff(*["# comment %d" % i for i in range(30)])
        result = _analyze(
            [
                (
                    "tests/test_app.py",
                    patch,
                    _row("tests/test_app.py", categories=("test", "source")),
                )
            ]
        )
        assert result.stats.comment_lines_added == 30

    def test_length_mismatch_is_rejected(self) -> None:
        files = [_changed("src/app.py", None)]
        rows = [_row("src/app.py"), _row("src/other.py")]
        with pytest.raises(ValueError, match="one-to-one"):
            analyze_added_comments(files, rows, _policy())

    def test_filename_mismatch_is_rejected(self) -> None:
        files = [_changed("generated/model.py", _diff("# generated"))]
        rows = [_row("src/human.py")]
        with pytest.raises(ValueError, match="pair by filename"):
            analyze_added_comments(files, rows, _policy())


# --- configuration --------------------------------------------------------------


class TestCodeCommentConfig:
    def test_issue_yaml_example_parses(self) -> None:
        yaml_text = """
policy:
  code_comments:
    enabled: true
    warn:
      max_block_lines: 10
      max_total_lines: 60
      max_comment_ratio: 0.45
    fail:
      max_block_lines: 25
      max_total_lines: 150
      max_comment_ratio: 0.70
    min_added_source_lines: 20
"""
        result = load_config(yaml_text)
        assert result.warnings == []
        policy = result.config.policy.code_comments
        assert policy.enabled is True
        assert policy.warn.max_block_lines == 10
        assert policy.warn.max_total_lines == 60
        assert policy.warn.max_comment_ratio == 0.45
        assert policy.fail.max_block_lines == 25
        assert policy.fail.max_total_lines == 150
        assert policy.fail.max_comment_ratio == 0.70
        assert policy.min_added_source_lines == 20

    def test_defaults_match_issue_143(self) -> None:
        policy = ReviewGateConfig().policy.code_comments
        assert policy.enabled is True
        assert policy.warn.max_block_lines == 10
        assert policy.warn.max_total_lines == 60
        assert policy.warn.max_comment_ratio == 0.45
        assert policy.fail.max_block_lines == 25
        assert policy.fail.max_total_lines == 150
        assert policy.fail.max_comment_ratio == 0.70
        assert policy.min_added_source_lines == 20

    @pytest.mark.parametrize(
        ("warn_key", "warn_value", "fail_key", "fail_value"),
        [
            ("max_block_lines", 30, "max_block_lines", 25),
            ("max_total_lines", 200, "max_total_lines", 150),
            ("max_comment_ratio", 0.9, "max_comment_ratio", 0.70),
        ],
    )
    def test_warn_above_fail_is_rejected(
        self,
        warn_key: str,
        warn_value: float,
        fail_key: str,
        fail_value: float,
    ) -> None:
        # Built directly (not via the test helper, which auto-raises fail
        # thresholds) so the cross-field validation itself is exercised.
        with pytest.raises(ValueError, match="must not exceed"):
            CodeCommentPolicy.model_validate(
                {"warn": {warn_key: warn_value}, "fail": {fail_key: fail_value}}
            )

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"warn_max_block_lines": -1}, "greater than or equal"),
            ({"warn_max_total_lines": -5}, "greater than or equal"),
            ({"warn_max_comment_ratio": -0.1}, "greater than or equal"),
            ({"warn_max_comment_ratio": 1.5}, "less than or equal"),
            ({"fail_max_block_lines": -1}, "greater than or equal"),
            ({"min_added_source_lines": -3}, "greater than or equal"),
        ],
    )
    def test_out_of_range_values_are_rejected(
        self, kwargs: dict[str, object], match: str
    ) -> None:
        warn = {k[len("warn_") :]: v for k, v in kwargs.items() if k.startswith("warn_")}
        fail = {k[len("fail_") :]: v for k, v in kwargs.items() if k.startswith("fail_")}
        top_level = {k: v for k, v in kwargs.items() if not k.startswith(("warn_", "fail_"))}
        with pytest.raises(ValueError, match=match):
            CodeCommentPolicy.model_validate({"warn": warn, "fail": fail, **top_level})

    def test_unknown_key_under_code_comments_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_comment_lines"):
            CodeCommentPolicy.model_validate({"warn": {"max_comment_lines": 5}})

    def test_disabled_policy_round_trips(self) -> None:
        result = load_config("policy:\n  code_comments:\n    enabled: false\n")
        assert result.warnings == []
        assert result.config.policy.code_comments.enabled is False

    def test_malformed_code_comments_config_falls_back_to_defaults(self) -> None:
        yaml_text = (
            "policy:\n"
            "  code_comments:\n"
            "    warn:\n"
            "      max_block_lines: 50\n"
            "    fail:\n"
            "      max_block_lines: 25\n"
        )
        result = load_config(yaml_text)
        assert [w.code for w in result.warnings] == [CONFIG_WARNING_CODE]
        assert result.config == ReviewGateConfig()


# --- engine integration -------------------------------------------------------------

_SUBSTANTIVE_BODY: Final[str] = (
    "Closes #1.\n\n"
    "This pull request implements a focused improvement to the API: it adds "
    "caching for the user activity endpoint and updates the matching unit "
    "tests so the reviewer can confirm the new behaviour without spinning up "
    "a full environment."
)


def _pr_record(additions: int, deletions: int = 0, changed_files_override: int = 1) -> PRRecord:
    return PRRecord(
        title="t",
        body=_SUBSTANTIVE_BODY,
        author="octocat",
        base_branch="main",
        head_branch="feat",
        additions=additions,
        deletions=deletions,
        changed_files=changed_files_override,
    )


def _engine_input(patch: str, filename: str = "src/app.py") -> EngineInput:
    lines = len([line for line in patch.splitlines() if line.startswith("+")])
    return EngineInput(
        pr=_pr_record(additions=lines, changed_files_override=2),
        files=[
            _changed(filename, patch, changes=lines),
            # A test file keeps the unrelated missing-tests heuristic silent.
            _changed("tests/test_app.py", None),
        ],
    )


def _with_config(engine_input: EngineInput, config: ReviewGateConfig) -> EngineInput:
    return EngineInput(
        pr=engine_input.pr,
        files=engine_input.files,
        config=config.model_dump(mode="json"),
    )


class TestEngineIntegration:
    def test_single_medium_warning_keeps_verdict_pass(self) -> None:
        """One medium does not move the baseline ladder (§10.13)."""

        patch = _diff(
            *["# c%d" % i for i in range(12)],
            *["x = %d" % i for i in range(20)],
        )
        report = analyze(_engine_input(patch))
        assert any(w.code == WARN_CODE_OVERSIZED_BLOCK for w in report.warnings)
        assert report.reviewability == "PASS"

    def test_two_medium_code_comment_warnings_aggregate_to_warn(self) -> None:
        """A 12-line block (medium) plus a 0.5 ratio (medium) via a custom
        policy; both flow into the ordinary aggregation."""

        config = load_config(
            "policy:\n"
            "  code_comments:\n"
            "    warn:\n"
            "      max_block_lines: 10\n"
            "      max_total_lines: 1000\n"
            "      max_comment_ratio: 0.5\n"
            "    fail:\n"
            "      max_block_lines: 25\n"
            "      max_total_lines: 1000\n"
            "      max_comment_ratio: 0.9\n"
            "    min_added_source_lines: 20\n"
        ).config
        patch = _diff(
            *["# c%d" % i for i in range(12)],
            *["x = %d" % i for i in range(12)],
        )
        report = analyze(_with_config(_engine_input(patch), config))
        medium_codes = {w.code for w in report.warnings if w.severity == "medium"}
        assert WARN_CODE_OVERSIZED_BLOCK in medium_codes
        assert WARN_CODE_COMMENT_HEAVY in medium_codes
        assert report.reviewability == "WARN"

    def test_stats_include_comment_volume_keys(self) -> None:
        patch = _diff("# one", "# two", "x = 1")
        report = analyze(_engine_input(patch))
        assert report.stats["comment_lines_added"] == 2
        assert report.stats["code_lines_added"] == 1
        assert report.stats["largest_comment_block_lines"] == 2
        assert report.stats["comment_ratio"] == round(2 / 3, 4)

    def test_disabled_policy_omits_warnings_and_stats_keys(self) -> None:
        config = load_config("policy:\n  code_comments:\n    enabled: false\n").config
        patch = _diff(*["# c%d" % i for i in range(30)])
        report = analyze(_with_config(_engine_input(patch), config))
        assert not any(
            w.code
            in (
                WARN_CODE_OVERSIZED_BLOCK,
                WARN_CODE_EXCESSIVE_LINES,
                WARN_CODE_COMMENT_HEAVY,
            )
            for w in report.warnings
        )
        assert "comment_lines_added" not in report.stats

    def test_high_severity_flows_into_aggregation_without_new_verdict_path(self) -> None:
        """A high code-comment warning alone must not FAIL the PR; the
        existing §10.13 ladder is untouched by the new heuristic."""

        patch = _diff(
            *["# c%d" % i for i in range(25)],
            *["x = %d" % i for i in range(60)],
        )
        report = analyze(_engine_input(patch))
        block_warning = next(w for w in report.warnings if w.code == WARN_CODE_OVERSIZED_BLOCK)
        assert block_warning.severity == "high"
        assert report.reviewability == baseline_reviewability(report.warnings)
        assert report.reviewability == "WARN"
