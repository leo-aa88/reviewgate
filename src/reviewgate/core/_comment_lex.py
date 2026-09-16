"""Language profiles and the conservative line scanner behind issue #143.

This module is private to ``reviewgate.core``. It owns the *syntax* half of
the excessive-comment-verbosity heuristic: which languages are modeled, and
how one post-image line is classified as comment, code, or blank while
carrying lexical state forward. The diff-walking half lives in
:mod:`reviewgate.core._comment_scan`, and the public policy surface in
:mod:`reviewgate.core.code_comments`.

Conservative-parsing contract (issue #143: false negatives are preferable
to noisy false positives):

* A line counts as a comment only when it is an *unmistakable full-line*
  comment (``#``, ``//``, or a ``/* ... */`` block line) at a lexical
  position where a comment can exist.
* Anything ambiguous -- trailing comments, string content, docstrings --
  falls into the code bucket or is ignored outright, never into commentary.
* The scanner tracks string literals (JS/Go backtick strings, Python
  triple-quoted strings, shell quotes and heredocs) and ``/* */`` blocks so
  markers inside string content are never miscounted.

Pure: stdlib only, no I/O, no GitHub or LLM dependency (§4.1 boundary).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

# --- language profiles ------------------------------------------------------
#
# Extension -> comment syntax profile. Extensions outside this map are not
# analyzed at all: guessing comment syntax for unsupported languages would
# risk exactly the false positives the issue forbids.

_SHELL_EXTENSIONS: Final[frozenset[str]] = frozenset({".sh", ".bash", ".zsh"})
_PYTHON_EXTENSIONS: Final[frozenset[str]] = frozenset({".py"})
# Only extensions whose multiline string forms this scanner actually models.
# JSX/TSX text, Rust/C++/C#/Java raw strings and text blocks are omitted
# until they can be classified without false positives (issue #143).
_JS_GO_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".js", ".mjs", ".cjs", ".ts", ".go"}
)


@dataclass(frozen=True)
class _Profile:
    """Comment-syntax profile applied by the line scanner."""

    hash_comments: bool  # `#` line comments (Python, Shell)
    c_comments: bool  # `//` line comments and `/* */` block comments
    triple_strings: bool  # Python triple-quoted strings
    backtick_strings: bool = False  # JS template literals / Go raw / shell `` ` ``
    shell_quoting: bool = False  # quotes and heredocs span lines without `\`


_PYTHON_PROFILE: Final[_Profile] = _Profile(
    hash_comments=True, c_comments=False, triple_strings=True
)
_SHELL_PROFILE: Final[_Profile] = _Profile(
    hash_comments=True,
    c_comments=False,
    triple_strings=False,
    backtick_strings=True,
    shell_quoting=True,
)
_JS_GO_PROFILE: Final[_Profile] = _Profile(
    hash_comments=False,
    c_comments=True,
    triple_strings=False,
    backtick_strings=True,
)


def _profile_for(filename: str) -> _Profile | None:
    """Return the comment profile for a path, or ``None`` if unsupported."""

    base = filename.rsplit("/", 1)[-1]
    dot = base.rfind(".")
    if dot <= 0:
        return None
    ext = base[dot:].lower()
    if ext in _PYTHON_EXTENSIONS:
        return _PYTHON_PROFILE
    if ext in _SHELL_EXTENSIONS:
        return _SHELL_PROFILE
    if ext in _JS_GO_EXTENSIONS:
        return _JS_GO_PROFILE
    return None


# --- conservative line scanner ----------------------------------------------


@dataclass
class _ScanState:
    """Lexical state carried across post-image lines of one hunk.

    Context lines update this state without contributing to metrics. A hunk
    whose entry position is unknown starts from a default instance but is
    not tallied until its own context establishes the position.
    """

    in_block_comment: bool = False  # inside a `/* ... */` block (C-like)
    triple_delimiter: str | None = None  # inside `"""` / `'''` (Python)
    in_string: str | None = None  # active quote: `"`, `'`, or `` ` ``
    string_raw: bool = False  # True: closer is unescaped (shell single quotes)
    heredoc_delimiter: str | None = None  # shell heredoc body until this word
    heredoc_strip: bool = False  # `<<-` strips leading tabs on the closer


_KIND_COMMENT: Final[str] = "comment"
_KIND_CODE: Final[str] = "code"
_KIND_BLANK: Final[str] = "blank"

_PatchKind = Literal["added", "context", "gap"]


def _state_is_clean(state: _ScanState) -> bool:
    """True when no multi-line construct is open at this lexical position.

    Used to decide whether a context line has actually told us anything: a
    line scanned while a construct is open or that leaves one open carries
    no evidence about where the hunk started.
    """

    return (
        state.in_string is None
        and state.triple_delimiter is None
        and state.heredoc_delimiter is None
        and not state.in_block_comment
    )


_CODE_EVIDENCE: Final[re.Pattern[str]] = re.compile(
    r"=|->|=>|::|\(|\)|\{|\}|\[|\]|;"
    r"|\b(?:def|func|struct|interface|package|typedef|namespace"
    r"|template|defer|raise|yield|async|await|elif|except)\b"
)
"""A token that a line of ordinary prose is very unlikely to contain.

Established hunk-entry state (see :data:`_comment_scan._CONTEXT_LINES_TO_ESTABLISH`)
must rest on positive evidence, not merely on the absence of a scanning
error. Operators carry almost all of the signal here: assignment, call and
index syntax, statement terminators, and arrow/scope forms appear in
essentially every real code line and essentially never in two consecutive
lines of English prose. The keyword arm covers the common cases an operator
misses (``def f():`` has one, but ``yield value`` and ``defer close()``
would not).

Deliberately excluded because they are ordinary English words and would let
docstring prose masquerade as code: ``if``, ``for``, ``in``, ``as``, ``and``,
``or``, ``not``, ``end``, ``case``, ``set``, ``do``, ``class``, ``return``,
``import``, ``let``, ``from``. Their code occurrences almost always carry an
operator anyway (``if err != nil {``), so nothing is lost.
"""


def _line_shows_code(line: str) -> bool:
    """True when a post-image line carries positive evidence of code.

    A line of docstring prose and a line of code are indistinguishable to
    the scanner when the hunk's entry state is unknown, because a construct
    opened above Git's context window is invisible. This predicate breaks
    that tie in the conservative direction: a line that shows no code token
    is not allowed to count as evidence that the hunk starts at a normal
    code position.
    """

    return _CODE_EVIDENCE.search(line) is not None


@dataclass(frozen=True)
class _PatchLine:
    """One unified-diff line classified for scanning vs tallying."""

    kind: _PatchKind
    content: str  # empty for gap


def _scan_line(line: str, state: _ScanState, profile: _Profile) -> str:
    """Classify one post-image line as ``comment`` / ``code`` / ``blank``.

    Updates ``state`` in place so subsequent lines of the same file (added
    *or* context) are scanned in the right context. Only whole-line comment
    forms are ever returned as :data:`_KIND_COMMENT`; trailing comments,
    string content, and anything ambiguous fall into :data:`_KIND_CODE`
    (the conservative bucket: non-blank, non-comment lines count as source
    lines when the caller tallies an added line).
    """

    stripped = line.strip()
    if not stripped:
        # A blank line never changes lexical state (a blank inside a block
        # comment, string, or heredoc leaves it open) and terminates
        # comment-block runs at the caller.
        return _KIND_BLANK

    if state.heredoc_delimiter is not None:
        closer = line.lstrip("\t") if state.heredoc_strip else line
        if closer == state.heredoc_delimiter:
            state.heredoc_delimiter = None
            state.heredoc_strip = False
        return _KIND_CODE

    i = 0
    n = len(line)
    while i < n and line[i] in (" ", "\t"):
        i += 1

    if state.in_string is not None:
        i = _scan_string_rest(line, 0, state)
        if state.in_string is not None or i >= len(line):
            return _KIND_CODE
        _scan_normal(line, i, state, profile)
        return _KIND_CODE

    if state.triple_delimiter is not None:
        # Inside a Python triple-quoted string: string content is never a
        # comment (docstrings are string literals, possibly runtime data).
        i = _scan_triple_rest(line, i, state)
        if state.triple_delimiter is not None or i >= len(line):
            return _KIND_CODE
        _scan_normal(line, i, state, profile)
        return _KIND_CODE

    if state.in_block_comment:
        close = line.find("*/", i)
        if close == -1:
            return _KIND_COMMENT
        state.in_block_comment = False
        # Code after the close is possible (`*/ int x;`): keep scanning the
        # remainder for state fidelity, but the line is not a full-line
        # comment.
        _scan_normal(line, close + 2, state, profile)
        return _KIND_CODE if line[close + 2 :].strip() else _KIND_COMMENT

    if profile.hash_comments and line[i] == "#":
        if line[i : i + 2] == "#!":
            # Shebang: an interpreter directive, not commentary.
            return _KIND_CODE
        return _KIND_COMMENT

    if profile.c_comments and line[i : i + 2] == "//":
        return _KIND_COMMENT

    if profile.c_comments and line[i : i + 2] == "/*":
        close = line.find("*/", i + 2)
        if close == -1:
            state.in_block_comment = True
            return _KIND_COMMENT
        if not line[close + 2 :].strip():
            return _KIND_COMMENT
        _scan_normal(line, close + 2, state, profile)
        return _KIND_CODE

    return _scan_normal(line, i, state, profile)


def _scan_normal(line: str, i: int, state: _ScanState, profile: _Profile) -> str:
    """Walk a line from index ``i`` through normal (non-comment) context.

    Consumes string literals so comment markers inside them are ignored,
    opens ``/* */`` blocks, Python triple-quoted strings, backtick strings,
    and shell heredocs that continue onto later lines, and always returns
    :data:`_KIND_CODE` -- by the time this function runs, the line has
    already shown real (non-comment) content or its leading token was not
    an unmistakable comment form.
    """

    n = len(line)
    while i < n:
        ch = line[i]
        if profile.triple_strings and line[i : i + 3] in ('"""', "'''"):
            delimiter = line[i : i + 3]
            end = _find_triple_close(line, i + 3, delimiter)
            if end == -1:
                state.triple_delimiter = delimiter
                return _KIND_CODE
            i = end
            continue
        if ch in ("'", '"'):
            raw = profile.shell_quoting and ch == "'"
            close = _find_quote_close(line, i + 1, ch, raw=raw)
            if close == -1:
                if profile.shell_quoting or _ends_with_backslash(line):
                    state.in_string = ch
                    state.string_raw = raw
                return _KIND_CODE
            i = close + 1
            continue
        if profile.backtick_strings and ch == "`":
            close = _find_quote_close(line, i + 1, "`", raw=False)
            if close == -1:
                state.in_string = "`"
                state.string_raw = False
                return _KIND_CODE
            i = close + 1
            continue
        if profile.c_comments and ch == "/":
            if line[i : i + 2] == "//":
                return _KIND_CODE
            if line[i : i + 2] == "/*":
                close = line.find("*/", i + 2)
                if close == -1:
                    state.in_block_comment = True
                    return _KIND_CODE
                i = close + 2
                continue
        if profile.hash_comments and ch == "#":
            # Trailing hash comment: rest of the line is not code, so a
            # `# cat <<EOF` comment must not open a heredoc.
            return _KIND_CODE
        if profile.shell_quoting:
            opened = _try_open_heredoc(line, i, state)
            if opened is not None:
                i = opened
                continue
        i += 1
    return _KIND_CODE


def _find_quote_close(line: str, start: int, quote: str, *, raw: bool) -> int:
    """Index of the next closer, or ``-1`` if it does not appear on this line.

    When ``raw`` is false, a backslash skips the next character (JS template
    literals, Python/C strings, shell double quotes). Go raw strings have no
    escapes; honouring ``\\`` there is a false-negative (preferred).
    """

    i = start
    n = len(line)
    while i < n:
        if not raw and line[i] == "\\":
            i += 2
            continue
        if line[i] == quote:
            return i
        i += 1
    return -1


def _scan_string_rest(line: str, i: int, state: _ScanState) -> int:
    """Consume the rest of a line inside a carried-over string literal.

    Returns the index to continue scanning from. If the string closes on
    this line, ``state`` returns to normal so the caller can re-scan the
    remainder.
    """

    quote = state.in_string
    if quote is None:  # pragma: no cover - guarded by caller
        return i
    close = _find_quote_close(line, i, quote, raw=state.string_raw)
    if close == -1:
        return len(line)
    state.in_string = None
    state.string_raw = False
    return close + 1


def _ends_with_backslash(line: str) -> bool:
    """True when the line continues a quoted string with a trailing ``\\``."""

    stripped = line.rstrip(" \t")
    count = 0
    idx = len(stripped) - 1
    while idx >= 0 and stripped[idx] == "\\":
        count += 1
        idx -= 1
    return count % 2 == 1


def _try_open_heredoc(line: str, i: int, state: _ScanState) -> int | None:
    """Open a shell heredoc starting at ``i`` and return the resume index.

    Recognises ``<<EOF``, ``<<-EOF``, ``<<'EOF'``, ``<<"EOF"``, ``<<\\EOF``,
    and the same forms with whitespace before the delimiter. ``<<<``
    here-strings are ignored. Returns ``None`` when ``i`` is not a heredoc
    operator; the caller then advances one character as usual.
    """

    if line[i : i + 2] != "<<":
        return None
    if line[i : i + 3] == "<<<":
        return None
    j = i + 2
    strip = False
    if j < len(line) and line[j] == "-":
        strip = True
        j += 1
    while j < len(line) and line[j] in (" ", "\t"):
        j += 1
    if j >= len(line):
        return None
    quote = ""
    if line[j] in ("'", '"', "\\"):
        quote = line[j]
        j += 1
    start = j
    while j < len(line) and (line[j].isalnum() or line[j] == "_"):
        j += 1
    if j == start:
        return None
    if quote in ("'", '"'):
        if j >= len(line) or line[j] != quote:
            return None
        j += 1
        state.heredoc_delimiter = line[start : j - 1]
    else:
        state.heredoc_delimiter = line[start:j]
    state.heredoc_strip = strip
    return j


def _find_triple_close(line: str, start: int, delimiter: str) -> int:
    """Index just past the first unescaped ``delimiter`` at/after ``start``.

    Returns ``-1`` when the delimiter does not terminate on this line, in
    which case the caller keeps the triple-quoted state open.
    """

    i = start
    n = len(line)
    while i < n:
        if line[i] == "\\":
            i += 2
            continue
        if line.startswith(delimiter, i):
            return i + len(delimiter)
        i += 1
    return -1


def _scan_triple_rest(line: str, i: int, state: _ScanState) -> int:
    """Consume the rest of a line inside a Python triple-quoted string.

    Returns the index to continue scanning from. If the string closes on
    this line, ``state`` returns to normal and the caller re-scans the
    remainder (which may open another string) so lexical state stays
    accurate for later lines.
    """

    delimiter = state.triple_delimiter
    if delimiter is None:  # pragma: no cover - guarded by caller
        return i
    end = _find_triple_close(line, i, delimiter)
    if end == -1:
        return len(line)
    state.triple_delimiter = None
    return end


__all__ = [
    "_KIND_BLANK",
    "_KIND_CODE",
    "_KIND_COMMENT",
    "_PatchKind",
    "_PatchLine",
    "_Profile",
    "_ScanState",
    "_line_shows_code",
    "_profile_for",
    "_scan_line",
    "_state_is_clean",
]


