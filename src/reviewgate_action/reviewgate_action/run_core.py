"""Run the deterministic engine on a fetched §10.1 EngineInput (issue #25).

Pipeline this module owns:

1. Read the §10.1 ``EngineInput`` JSON written by
   :mod:`reviewgate_action.fetch_pr` (or any other §10.1 producer).
2. If a ``.reviewgate.yml`` exists in the workspace, load it via
   :func:`reviewgate.core.config.load_config`. On parse / validation
   failure the loader returns the all-defaults config plus a single
   §12 low-severity warning; this module surfaces that warning in the
   final report alongside the engine's own warnings rather than
   crashing.
3. Materialise the loaded :class:`ReviewGateConfig` into the engine
   input's ``config`` block (as JSON) so the engine sees the same
   effective configuration the Action loaded.
4. Call :func:`reviewgate.core.engine.analyze`.
5. Print a human-readable summary (verdict, warning ladder, suggested
   labels, file-category counts) to stderr **and** to
   ``$GITHUB_STEP_SUMMARY`` when set, plus the full §10.2 JSON
   report to stdout. The split keeps stdout machine-parseable for
   downstream Action steps while the human summary lights up the
   workflow log.
6. Apply the §14 ``fail-on`` policy: exit 0 when the verdict is
   below the threshold, exit 1 when the verdict reaches it. The
   ``never`` value disables the fail behaviour for a quiet/auto-mode
   rollout (§14.1) without removing the report.

Pure boundary: this module does I/O (file reads, stdout, optional
``$GITHUB_STEP_SUMMARY``) but never imports the engine's I/O-banned
modules. ``reviewgate.core`` stays pure; the Action wraps it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from reviewgate.core.config import ConfigMode, load_config
from reviewgate.core.engine import analyze
from reviewgate.core.schemas import (
    EngineInput,
    EngineWarning,
    Reviewability,
    ReviewabilityReport,
)
from reviewgate_action import coexistence, post_comment

_PROG: Final[str] = "reviewgate-action.run_core"

_EXIT_OK: Final[int] = 0
_EXIT_FAIL_ON: Final[int] = 1
"""Exit code when the verdict reaches the ``fail-on`` threshold."""
_EXIT_USAGE: Final[int] = 2
"""Exit code for unusable inputs (missing/malformed file, env, schema)."""

_FailOn = str
"""Free-form for argparse; validated against :data:`_FAIL_ON_VALUES`."""

_FAIL_ON_VALUES: Final[tuple[str, ...]] = ("PASS", "WARN", "FAIL", "never")
"""§14 ``fail-on`` enum. ``never`` disables the fail behaviour."""

_DEFAULT_CONFIG_FILENAME: Final[str] = ".reviewgate.yml"


# Ladder used for `fail-on` comparisons. PASS < WARN < FAIL; `never`
# is handled separately (it disables the comparison entirely).
_VERDICT_RANK: Final[dict[Reviewability, int]] = {
    "PASS": 0,
    "WARN": 1,
    "FAIL": 2,
}


# --- argparse --------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=(
            "Run the deterministic reviewability engine on a fetched §10.1 "
            "EngineInput JSON document, optionally loading `.reviewgate.yml` "
            "for repo-specific config, and apply the §14 `fail-on` policy."
        ),
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help=(
            "Path to a §10.1 EngineInput JSON document, typically the "
            "output of `python -m reviewgate_action.fetch_pr`."
        ),
    )
    parser.add_argument(
        "--config-file",
        default=_DEFAULT_CONFIG_FILENAME,
        help=(
            "Path to `.reviewgate.yml` relative to the workspace root "
            f"(default: {_DEFAULT_CONFIG_FILENAME!r}). Missing files are "
            "silently treated as 'no config' per §12."
        ),
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help=(
            "Workspace root used to resolve `--config-file` when it is a "
            "relative path. Defaults to `$GITHUB_WORKSPACE` or the current "
            "working directory."
        ),
    )
    parser.add_argument(
        "--fail-on",
        choices=_FAIL_ON_VALUES,
        default="FAIL",
        help=(
            "Verdict at or above which the run exits non-zero. One of "
            "PASS / WARN / FAIL / never (default: FAIL per §14)."
        ),
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help=(
            "Optional path to write the §10.2 ReviewabilityReport JSON to. "
            "When omitted the JSON also goes to stdout (always written to "
            "stdout regardless of this flag so callers that pipe still work)."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "action", "quiet"),
        default="auto",
        help=(
            "§14.1 coexistence mode with the hosted ReviewGate App. "
            "`auto` (default) defers to `.reviewgate.yml`'s `mode`; "
            "`action` forces the Action to own posting; `quiet` skips "
            "posting and ignores `--fail-on`."
        ),
    )
    parser.add_argument(
        "--post-comment",
        choices=("true", "false"),
        default="true",
        help=(
            "When `true` (the default) and §14.1 coexistence allows, "
            "upsert the §13 ReviewGate marker comment on the PR. "
            "Requires `pull-requests: write` on the GitHub token."
        ),
    )
    parser.add_argument(
        "--repo",
        default=None,
        help=(
            "GitHub repository in `owner/repo` form. Defaults to the "
            "`GITHUB_REPOSITORY` env var. Required when `--post-comment` "
            "resolves to true."
        ),
    )
    parser.add_argument(
        "--pull-number",
        type=int,
        default=None,
        help=(
            "PR number for the comment upsert. Defaults to the value "
            "embedded in `$GITHUB_EVENT_PATH`'s payload. Required when "
            "`--post-comment` resolves to true."
        ),
    )
    return parser


# --- config loading --------------------------------------------------


def _resolve_config_path(workspace: str | None, config_file: str) -> Path:
    """Resolve ``--config-file`` against the workspace root.

    Mirrors how GitHub Actions exposes the checked-out repo: the
    composite step normally runs in the repo root, so a default
    ``.reviewgate.yml`` lookup hits the right file. Tests that
    explicitly want to control the location pass an absolute path,
    in which case this helper returns it unchanged.
    """

    candidate = Path(config_file)
    if candidate.is_absolute():
        return candidate
    root = workspace or os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    return Path(root) / candidate


def _read_config_text(path: Path) -> str | None:
    """Return the YAML text at ``path`` or ``None`` when missing.

    Distinguishing "missing" from "empty" matters: ``load_config``
    treats an empty / whitespace-only string the same as ``None``,
    so a repo that ships a `.reviewgate.yml` containing only
    comments still gets the all-defaults configuration without a
    parse warning.
    """

    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


# --- engine wiring ---------------------------------------------------


def _read_engine_input(path: Path) -> EngineInput:
    """Read and validate the §10.1 input document at ``path``.

    Errors are mapped onto :class:`RuntimeError` with stable prefixes
    so :func:`main` can emit a uniform, parseable error line and exit
    with the documented usage code.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"engine input file not found: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"could not read engine input file {path}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"engine input at {path} is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"engine input at {path} must be a JSON object; got {type(payload).__name__}"
        )

    return _validated_engine_input(payload)


def _validated_engine_input(payload: dict[str, Any]) -> EngineInput:
    try:
        return EngineInput.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeError(f"engine input does not match §10.1 EngineInput schema:\n{exc}") from exc


def _merge_loaded_config(
    payload: dict[str, Any],
    *,
    workspace: str | None,
    config_file: str,
) -> tuple[dict[str, Any], list[EngineWarning], Path, ConfigMode]:
    """Apply the loaded `.reviewgate.yml` to ``payload['config']``.

    Returns ``(payload, warnings, resolved_path, config_mode)`` where
    ``warnings`` is the §12 config-load warning list (zero entries
    on success, one entry on parse / validation failure with severity
    ``low``), ``resolved_path`` is the absolute path the loader
    inspected, and ``config_mode`` is the effective ``.reviewgate.yml``
    ``mode`` value (defaulting to ``"app"`` per §12 when the file is
    missing or malformed). The payload is mutated in place and also
    returned so callers can chain.
    """

    resolved = _resolve_config_path(workspace, config_file)
    yaml_text = _read_config_text(resolved)
    result = load_config(yaml_text, source_path=str(resolved))
    payload["config"] = result.config.model_dump(mode="json")
    return payload, list(result.warnings), resolved, result.config.mode


def _prepend_config_warnings(
    report: ReviewabilityReport,
    warnings: list[EngineWarning],
) -> ReviewabilityReport:
    """Add §12 config warnings ahead of the engine warnings, in order.

    Config warnings are surfaced first because they describe an
    operator-fixable problem (the YAML on disk) that may explain
    other warnings, e.g. a missing custom risky-paths list.
    """

    if not warnings:
        return report
    return report.model_copy(update={"warnings": list(warnings) + list(report.warnings)})


# --- summary rendering ----------------------------------------------


_VERDICT_GLYPH: Final[dict[Reviewability, str]] = {
    "PASS": "[PASS]",
    "WARN": "[WARN]",
    "FAIL": "[FAIL]",
}

# Markdown bullet label for ``stats["human_loc_changed"]`` (DESIGN §10.4).
# Wording avoids implying every line was typed by a PR author; the JSON
# key remains ``human_loc_changed`` for downstream parsers.
_SUMMARY_STAT_LABEL_HUMAN_LOC: Final[str] = "LOC after §10.4 exclusions (`human_loc_changed`)"

# Short blurbs for ``stats["pr_author_kind"]`` (DESIGN §10.4.2); keys match engine output.
_SUMMARY_PR_AUTHOR_KIND_LABEL: Final[dict[str, str]] = {
    "human": "human collaborator account",
    "dependency_automation": "dependency automation (Dependabot / Renovate)",
    "coding_agent_automation": "coding-agent integration account",
    "generic_automation": "other GitHub App / bot account (`[bot]` suffix)",
}


def render_summary(report: ReviewabilityReport) -> str:
    """Render a Markdown-flavoured human summary of ``report``.

    The output is consumed by:

    * the workflow log (we write it to stderr so stdout stays
      reserved for the JSON document); and
    * `$GITHUB_STEP_SUMMARY`, where Markdown renders into the
      "Summary" panel for each job.

    Kept in its own helper so tests can assert against the exact
    rendering without re-running the whole pipeline.

    Note:
        The stats bullet uses :data:`_SUMMARY_STAT_LABEL_HUMAN_LOC` so the
        workflow log matches DESIGN §10.4 semantics while stdout JSON
        keeps the stable ``human_loc_changed`` field name.
    """

    lines: list[str] = []
    glyph = _VERDICT_GLYPH[report.reviewability]
    lines.append(f"## ReviewGate {glyph} `{report.reviewability}`")
    lines.append("")

    stats = report.stats
    files_changed = stats.get("files_changed")
    raw_loc = stats.get("raw_loc_changed")
    human_loc = stats.get("human_loc_changed")
    author_kind = stats.get("pr_author_kind")
    show_numeric_stats = any(v is not None for v in (files_changed, raw_loc, human_loc))
    show_author_stats = isinstance(author_kind, str)
    if show_numeric_stats or show_author_stats:
        lines.append("**Stats**")
        lines.append("")
        if show_numeric_stats:
            lines.append(f"- Files changed: `{files_changed}`")
            lines.append(f"- Raw LOC changed: `{raw_loc}`")
            lines.append(f"- {_SUMMARY_STAT_LABEL_HUMAN_LOC}: `{human_loc}`")
        if show_author_stats:
            blurb = _SUMMARY_PR_AUTHOR_KIND_LABEL.get(author_kind, author_kind)
            login = stats.get("pr_author_login")
            login_suffix = (
                f" — login `{login}`" if isinstance(login, str) and login.strip() else ""
            )
            lines.append(
                f"- PR author class: `{author_kind}` ({blurb}){login_suffix} (§10.4.2)."
            )
        if stats.get("dependency_automation_manifest_only") is True:
            lines.append(
                "- Manifest-only dependency automation: "
                "``human_loc_changed`` clamped to ``0`` for §10.3 thresholds."
            )
        lines.append("")

    if report.warnings:
        lines.append(f"**Warnings ({len(report.warnings)})**")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- `{warning.severity}` `{warning.code}` -- {warning.message}")
        lines.append("")
    else:
        lines.append("No deterministic warnings fired.")
        lines.append("")

    if report.suggested_labels:
        joined = ", ".join(f"`{label}`" for label in report.suggested_labels)
        lines.append(f"**Suggested labels:** {joined}")
        lines.append("")

    if report.file_categories:
        risky = sum(1 for row in report.file_categories if row.risky)
        lines.append(f"**File categories:** {len(report.file_categories)} files ({risky} risky)")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _emit_summary(report: ReviewabilityReport) -> None:
    """Write the summary to stderr and to `$GITHUB_STEP_SUMMARY` if set."""

    rendered = render_summary(report)
    sys.stderr.write(rendered)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(rendered)
        except OSError as exc:
            # Failing to write the summary is non-fatal: the workflow
            # log already has the same content. Surface a single line
            # so an operator can debug it without breaking the run.
            sys.stderr.write(f"{_PROG}: could not write GITHUB_STEP_SUMMARY: {exc}\n")


# --- fail-on ---------------------------------------------------------


def exit_code_for_fail_on(fail_on: str, verdict: Reviewability) -> int:
    """Return the exit code the §14 ``fail-on`` policy implies.

    The threshold is inclusive: ``fail-on FAIL`` exits 1 only on a
    FAIL verdict; ``fail-on WARN`` exits 1 on WARN or FAIL; etc.
    ``never`` always returns 0 so a quiet rollout (§14.1
    ``mode: app``) can still publish the report without breaking the
    workflow.
    """

    if fail_on == "never":
        return _EXIT_OK
    if fail_on not in _VERDICT_RANK:
        raise RuntimeError(f"fail-on must be one of {_FAIL_ON_VALUES}; got {fail_on!r}")
    threshold = _VERDICT_RANK[fail_on]  # type: ignore[index]
    return _EXIT_FAIL_ON if _VERDICT_RANK[verdict] >= threshold else _EXIT_OK


# --- entry point -----------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m reviewgate_action.run_core``.

    Returns the exit code documented per outcome:

    * 0 on a successful run whose verdict is below ``--fail-on``,
      or when §14.1 coexistence put the Action in a quiet mode.
    * 1 on a successful run whose verdict reaches ``--fail-on``.
    * 2 on every documented input/usage error (missing file, bad
      JSON, schema mismatch, unknown ``fail-on`` value).
    """

    args = _build_parser().parse_args(argv)

    try:
        engine_input = _read_engine_input(Path(args.input))
        payload = engine_input.model_dump(mode="json")
        payload, config_warnings, resolved_path, config_mode = _merge_loaded_config(
            payload,
            workspace=args.workspace,
            config_file=args.config_file,
        )
        engine_input = _validated_engine_input(payload)
    except RuntimeError as exc:
        print(f"{_PROG}: {exc}", file=sys.stderr)
        return _EXIT_USAGE

    report = analyze(engine_input)
    report = _prepend_config_warnings(report, config_warnings)

    if args.config_file != _DEFAULT_CONFIG_FILENAME or resolved_path.exists():
        sys.stderr.write(f"{_PROG}: loaded config from {resolved_path}\n")

    decision = coexistence.decide(
        action_mode=args.mode,
        config_mode=config_mode,
        post_comment_input=(args.post_comment == "true"),
    )
    sys.stderr.write(f"{_PROG}: coexistence -- {decision.rationale}\n")

    serialized = report.model_dump_json(indent=2)
    sys.stdout.write(serialized)
    sys.stdout.write("\n")
    if args.output_json:
        Path(args.output_json).write_text(serialized + "\n", encoding="utf-8")

    _emit_summary(report)

    if decision.post_comment:
        post_comment.upsert_from_environment(
            report=report,
            summary_md=render_summary(report),
            repo_arg=args.repo,
            pull_arg=args.pull_number,
            log_prefix=_PROG,
        )

    if not decision.apply_fail_on:
        return _EXIT_OK
    return exit_code_for_fail_on(args.fail_on, report.reviewability)


if __name__ == "__main__":  # pragma: no cover - exercised via the module
    raise SystemExit(main())


__all__ = [
    "exit_code_for_fail_on",
    "main",
    "render_summary",
]
