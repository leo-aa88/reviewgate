"""Tests for ``reviewgate_action.run_core`` (issue #25).

The module is the open-source GitHub Action's invocation boundary
around the deterministic engine: it reads a §10.1 ``EngineInput``
JSON document (typically produced by ``reviewgate_action.fetch_pr``),
loads `.reviewgate.yml` if present, runs ``analyze``, prints a
human summary plus the §10.2 ``ReviewabilityReport`` JSON, and
applies the §14 ``fail-on`` policy.

These tests cover:

* Argparse contract (`--input`, `--config-file`, `--workspace`,
  `--fail-on`, `--output-json`).
* `.reviewgate.yml` resolution against the workspace root and the
  silent ``no config`` path on a missing file.
* §12 malformed-config recovery: the loader's low-severity warning
  must surface in the report instead of crashing the run.
* §14 `fail-on` policy across the verdict ladder, including the
  `never` escape hatch.
* Step-output friendly behaviour: stdout JSON, optional file copy,
  and `$GITHUB_STEP_SUMMARY` write.

All tests stay hermetic by writing fixture inputs to ``tmp_path``
and patching the engine entry point only when the test cares about
the *plumbing*, not the engine's verdict (the engine itself is
exhaustively covered by `tests/test_golden_fixtures.py`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest

from reviewgate.core.config import CONFIG_WARNING_CODE
from reviewgate_action import run_core


# Substantive body + issue reference are intentional: this fixture has
# to clear the §10.10 weak-body threshold (>= 80 meaningful chars) and
# the §10.11 linked-issue heuristic so the verdict is a clean PASS.
# Without the linked-issue line we'd pick up a `missing-context` label
# and the run_core summary tests would see a WARN instead.
_PASS_INPUT: Final[dict[str, Any]] = {
    "pr": {
        "title": "Tighten search index logging",
        "body": (
            "Adds debug-level logs around the search index warm-up path so "
            "operators can correlate slow startups with shard rebalances. "
            "Closes #321."
        ),
        "author": "octocat",
        "base_branch": "main",
        "head_branch": "feat/search-logs",
        "additions": 12,
        "deletions": 3,
        "changed_files": 1,
    },
    "files": [
        {
            "filename": "src/search/index.py",
            "status": "modified",
            "additions": 12,
            "deletions": 3,
            "changes": 15,
        }
    ],
    "config": {},
}


_FAIL_INPUT: Final[dict[str, Any]] = {
    "pr": {
        "title": "Massive refactor (no rationale)",
        "body": "",
        "author": "octocat",
        "base_branch": "main",
        "head_branch": "refactor/everything",
        "additions": 4500,
        "deletions": 800,
        "changed_files": 80,
    },
    "files": [
        {
            "filename": f"src/feature_{i}/module.py",
            "status": "modified",
            "additions": 60,
            "deletions": 10,
            "changes": 70,
        }
        for i in range(80)
    ],
    "config": {},
}


def _write(payload: dict[str, Any], target: Path) -> Path:
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


# --- argparse contract ----------------------------------------------


def test_input_flag_is_required(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        run_core.main([])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "--input" in captured.err


def test_unknown_fail_on_value_rejected_by_argparse(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload_path = _write(_PASS_INPUT, tmp_path / "engine.json")
    with pytest.raises(SystemExit) as exc:
        run_core.main(["--input", str(payload_path), "--fail-on", "MAYBE"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


# --- engine input handling ------------------------------------------


def test_missing_input_file_returns_usage_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_core.main(["--input", str(tmp_path / "missing.json")])
    assert code == 2
    captured = capsys.readouterr()
    assert "engine input file not found" in captured.err


def test_invalid_json_input_returns_usage_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "engine.json"
    bad.write_text("{not json", encoding="utf-8")
    code = run_core.main(["--input", str(bad)])
    assert code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_non_object_json_input_returns_usage_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "engine.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    code = run_core.main(["--input", str(bad)])
    assert code == 2
    assert "must be a JSON object" in capsys.readouterr().err


def test_engine_input_schema_violation_returns_usage_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = json.loads(json.dumps(_PASS_INPUT))
    payload["pr"]["additions"] = -1
    code = run_core.main(["--input", str(_write(payload, tmp_path / "engine.json"))])
    assert code == 2
    assert "EngineInput schema" in capsys.readouterr().err


# --- config resolution ----------------------------------------------


def test_missing_reviewgate_yml_is_silent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Absent `.reviewgate.yml` should not warn (§12 explicit semantics)."""

    payload_path = _write(_PASS_INPUT, tmp_path / "engine.json")
    code = run_core.main(
        [
            "--input",
            str(payload_path),
            "--workspace",
            str(tmp_path),
            "--fail-on",
            "FAIL",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    codes = [w["code"] for w in report["warnings"]]
    assert CONFIG_WARNING_CODE not in codes


def test_malformed_reviewgate_yml_surfaces_config_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bogus YAML file must produce the §12 ``config_invalid`` warning.

    The engine still runs against defaults; the warning rides along
    with severity ``low`` so the report stays useful.
    """

    (tmp_path / ".reviewgate.yml").write_text(
        "version: not-a-number\nthresholds: 'oops'\n",
        encoding="utf-8",
    )
    payload_path = _write(_PASS_INPUT, tmp_path / "engine.json")
    code = run_core.main(
        [
            "--input",
            str(payload_path),
            "--workspace",
            str(tmp_path),
            "--fail-on",
            "FAIL",
        ]
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    config_warnings = [
        w for w in report["warnings"] if w["code"] == CONFIG_WARNING_CODE
    ]
    assert len(config_warnings) == 1
    assert config_warnings[0]["severity"] == "low"


def test_well_formed_reviewgate_yml_loads_without_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid `.reviewgate.yml` must not emit ``config_invalid``."""

    (tmp_path / ".reviewgate.yml").write_text(
        "version: 1\n"
        "thresholds:\n"
        "  warn:\n"
        "    files_changed: 5\n"
        "  fail:\n"
        "    files_changed: 10\n",
        encoding="utf-8",
    )
    payload_path = _write(_PASS_INPUT, tmp_path / "engine.json")
    code = run_core.main(
        [
            "--input",
            str(payload_path),
            "--workspace",
            str(tmp_path),
        ]
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert all(w["code"] != CONFIG_WARNING_CODE for w in report["warnings"])


def test_absolute_config_file_overrides_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absolute ``--config-file`` must bypass the workspace root."""

    config_dir = tmp_path / "alt"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "version: 1\n", encoding="utf-8"
    )
    payload_path = _write(_PASS_INPUT, tmp_path / "engine.json")
    code = run_core.main(
        [
            "--input",
            str(payload_path),
            "--workspace",
            str(tmp_path),
            "--config-file",
            str(config_dir / "config.yml"),
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert str(config_dir / "config.yml") in captured.err


# --- fail-on policy --------------------------------------------------


@pytest.mark.parametrize(
    ("fail_on", "verdict", "expected"),
    [
        ("FAIL", "PASS", 0),
        ("FAIL", "WARN", 0),
        ("FAIL", "FAIL", 1),
        ("WARN", "PASS", 0),
        ("WARN", "WARN", 1),
        ("WARN", "FAIL", 1),
        ("PASS", "PASS", 1),
        ("PASS", "WARN", 1),
        ("PASS", "FAIL", 1),
        ("never", "PASS", 0),
        ("never", "WARN", 0),
        ("never", "FAIL", 0),
    ],
)
def test_exit_code_for_fail_on_implements_design_doc_ladder(
    fail_on: str, verdict: str, expected: int
) -> None:
    assert run_core.exit_code_for_fail_on(fail_on, verdict) == expected  # type: ignore[arg-type]


def test_exit_code_for_fail_on_rejects_unknown_value() -> None:
    with pytest.raises(RuntimeError, match="fail-on must be one of"):
        run_core.exit_code_for_fail_on("MAYBE", "PASS")


def test_main_returns_one_when_verdict_meets_fail_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end: a substantive fail-fixture exits 1 under default policy.

    Pairs the hardest §24.2 fixture with the documented default
    (``--fail-on FAIL``) so a regression that lets a FAIL verdict
    exit 0 is caught here as well as in the unit tests above.
    """

    payload_path = _write(_FAIL_INPUT, tmp_path / "engine.json")
    code = run_core.main(
        [
            "--input",
            str(payload_path),
            "--workspace",
            str(tmp_path),
            "--fail-on",
            "FAIL",
        ]
    )
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["reviewability"] == "FAIL"


def test_main_returns_zero_for_pass_under_default_fail_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload_path = _write(_PASS_INPUT, tmp_path / "engine.json")
    code = run_core.main(["--input", str(payload_path), "--workspace", str(tmp_path)])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["reviewability"] == "PASS"


def test_never_fail_on_keeps_exit_zero_even_for_fail_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`never` is the §14.1 quiet/auto-mode escape hatch."""

    payload_path = _write(_FAIL_INPUT, tmp_path / "engine.json")
    code = run_core.main(
        [
            "--input",
            str(payload_path),
            "--workspace",
            str(tmp_path),
            "--fail-on",
            "never",
        ]
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["reviewability"] == "FAIL"


# --- output / summary -----------------------------------------------


def test_output_json_writes_report_to_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload_path = _write(_PASS_INPUT, tmp_path / "engine.json")
    out_path = tmp_path / "report.json"
    code = run_core.main(
        [
            "--input",
            str(payload_path),
            "--workspace",
            str(tmp_path),
            "--output-json",
            str(out_path),
        ]
    )
    assert code == 0
    file_report = json.loads(out_path.read_text(encoding="utf-8"))
    stdout_report = json.loads(capsys.readouterr().out)
    assert file_report == stdout_report


def test_summary_written_to_github_step_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary_path = tmp_path / "summary.md"
    summary_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    payload_path = _write(_PASS_INPUT, tmp_path / "engine.json")
    code = run_core.main(["--input", str(payload_path), "--workspace", str(tmp_path)])
    assert code == 0
    capsys.readouterr()
    written = summary_path.read_text(encoding="utf-8")
    assert "## ReviewGate" in written
    assert "PASS" in written


def test_summary_skipped_silently_when_no_github_step_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Local `python -m` invocations have no `GITHUB_STEP_SUMMARY`.

    The Action runtime sets it automatically; a developer running
    the module from a shell will not. The summary must still go to
    stderr but the absence of the env var must not error out.
    """

    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    payload_path = _write(_PASS_INPUT, tmp_path / "engine.json")
    code = run_core.main(["--input", str(payload_path), "--workspace", str(tmp_path)])
    assert code == 0
    captured = capsys.readouterr()
    assert "ReviewGate" in captured.err


def test_render_summary_includes_warning_code_and_severity() -> None:
    """The Markdown summary must surface every warning code + severity.

    Workflow log readers grep for warning codes; the helper has to
    print them in a stable shape so a regex like ``too_large_human_loc``
    keeps matching.
    """

    from reviewgate.core.engine import analyze
    from reviewgate.core.schemas import EngineInput

    engine_input = EngineInput.model_validate(_FAIL_INPUT)
    report = analyze(engine_input)
    rendered = run_core.render_summary(report)
    assert "## ReviewGate" in rendered
    assert "FAIL" in rendered
    for warning in report.warnings:
        assert warning.code in rendered
        assert warning.severity in rendered
