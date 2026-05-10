"""Tests for the ``reviewgate-core`` CLI (docs/DESIGN.md \u00a75.1, \u00a725 M1).

Locks the \u00a725 Milestone-1 acceptance criteria for the CLI:

1. Reads stdin or a file path containing JSON matching \u00a710.1.
2. Prints \u00a710.2-shaped JSON to stdout.
3. Exits non-zero on invalid input schema; zero on a successful run.

Tests call :func:`reviewgate.core.cli.main` directly and assert on the
captured stdout/stderr plus the integer exit code so regressions are
diagnosed without spawning a subprocess.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Final

import pytest

from reviewgate.core.cli import main
from reviewgate.core.schemas import (
    EngineInput,
    Reviewability,
    ReviewabilityReport,
)

_EXIT_OK: Final[int] = 0
_EXIT_INVALID_INPUT: Final[int] = 2


def _minimal_engine_input_payload() -> dict[str, object]:
    """Return a minimal \u00a710.1 EngineInput payload as plain JSON dicts.

    Built via :class:`EngineInput` so the fixture stays in lockstep with
    the schema: any future required field shows up as a Pydantic
    validation error here instead of as a silent CLI regression.
    """

    sample = EngineInput.model_validate(
        {
            "pr": {
                "title": "Add docs",
                # Substantive body with an issue reference so neither
                # the \u00a710.10 weak-body heuristic (#11) nor the
                # missing-linked-issue heuristic (#12) fires; CLI tests
                # can keep asserting ``report.warnings == []``.
                "body": (
                    "Closes #1.\n\n"
                    "This PR rewrites the README intro section so new "
                    "contributors can find the install instructions, the "
                    "supported Python versions, and the test command "
                    "without scrolling. No code changes."
                ),
                "author": "octocat",
                "base_branch": "main",
                "head_branch": "feat/docs",
                "additions": 10,
                "deletions": 2,
                "changed_files": 1,
            },
            "files": [
                {
                    "filename": "README.md",
                    "status": "modified",
                    "additions": 10,
                    "deletions": 2,
                    "changes": 12,
                }
            ],
        },
    )
    return sample.model_dump(mode="json")


def _assert_pass_report_for(payload: dict[str, object], stdout_text: str) -> None:
    """Validate the captured stdout against the \u00a710.2 schema and stub semantics."""

    parsed = json.loads(stdout_text)
    report = ReviewabilityReport.model_validate(parsed)
    expected_pass: Reviewability = "PASS"
    assert report.reviewability == expected_pass
    assert report.warnings == []
    pr = payload["pr"]
    assert isinstance(pr, dict)
    assert report.stats["files_changed"] == pr["changed_files"]
    assert report.stats["additions"] == pr["additions"]
    assert report.stats["deletions"] == pr["deletions"]
    assert report.stats["raw_loc_changed"] == pr["additions"] + pr["deletions"]


def test_cli_reads_input_file_and_prints_pass_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--input PATH`` reads JSON from disk and prints a \u00a710.2 report."""

    payload = _minimal_engine_input_payload()
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(["--input", str(fixture)])

    assert exit_code == _EXIT_OK
    captured = capsys.readouterr()
    assert captured.err == ""
    _assert_pass_report_for(payload, captured.out)


def test_cli_reads_stdin_when_input_omitted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Omitting ``--input`` makes the CLI read stdin (pipe ergonomics)."""

    payload = _minimal_engine_input_payload()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    exit_code = main([])

    assert exit_code == _EXIT_OK
    captured = capsys.readouterr()
    _assert_pass_report_for(payload, captured.out)


def test_cli_treats_dash_as_explicit_stdin(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--input -`` is the explicit stdin form; same behavior as omitting it."""

    payload = _minimal_engine_input_payload()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    exit_code = main(["--input", "-"])

    assert exit_code == _EXIT_OK
    captured = capsys.readouterr()
    _assert_pass_report_for(payload, captured.out)


def test_cli_exits_two_on_non_json_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-JSON input must surface a useful stderr message and exit 2."""

    fixture = tmp_path / "broken.json"
    fixture.write_text("not-json::", encoding="utf-8")

    exit_code = main(["--input", str(fixture)])

    assert exit_code == _EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not valid JSON" in captured.err


def test_cli_exits_two_on_schema_violation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON that violates the \u00a710.1 schema must exit 2 with a Pydantic message."""

    fixture = tmp_path / "wrong-shape.json"
    fixture.write_text(json.dumps({"pr": {}, "files": []}), encoding="utf-8")

    exit_code = main(["--input", str(fixture)])

    assert exit_code == _EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "EngineInput schema" in captured.err


def test_cli_exits_two_on_unknown_top_level_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``extra='forbid'`` on EngineInput is propagated through the CLI."""

    payload = _minimal_engine_input_payload()
    payload["unexpected"] = True
    fixture = tmp_path / "extra-key.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(["--input", str(fixture)])

    assert exit_code == _EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert "EngineInput schema" in captured.err


def test_cli_exits_two_on_missing_input_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing ``--input`` path exits 2 with a friendly stderr line.

    We deliberately surface a CLI-style error rather than letting the
    underlying :class:`FileNotFoundError` traceback escape; the exit
    code matches every other unusable-input case so callers (CI scripts,
    the GitHub Action wrapper) can rely on a single failure signal.
    """

    missing = tmp_path / "does-not-exist.json"

    exit_code = main(["--input", str(missing)])

    assert exit_code == _EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "input file not found" in captured.err
    assert str(missing) in captured.err


def test_cli_exits_two_when_input_path_is_a_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pointing ``--input`` at a directory exits 2 instead of crashing.

    Same contract as the missing-file case: the CLI reduces every
    OS-level read failure to "unusable input" with exit code 2.
    """

    exit_code = main(["--input", str(tmp_path)])

    assert exit_code == _EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read input" in captured.err


def test_cli_help_does_not_raise(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--help`` exits cleanly via :class:`SystemExit` (argparse contract)."""

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == _EXIT_OK
    captured = capsys.readouterr()
    assert "EngineInput" in captured.out
    assert "fixture JSON" in captured.out


def test_cli_output_is_pretty_printed_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stdout uses 2-space indented JSON so humans can read fixture runs."""

    payload = _minimal_engine_input_payload()
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    main(["--input", str(fixture)])

    captured = capsys.readouterr()
    assert "\n  " in captured.out, "expected indented JSON output"
    assert captured.out.endswith("\n")
