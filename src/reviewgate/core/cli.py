"""CLI entrypoint for fixture-driven runs (docs/DESIGN.md \u00a75.1, \u00a725 M1).

Usage::

    reviewgate-core --input fixture.json
    reviewgate-core < fixture.json
    cat fixture.json | reviewgate-core

Reads a \u00a710.1-shaped JSON document from ``--input`` (or stdin when
omitted), validates it via :class:`reviewgate.core.schemas.EngineInput`,
runs the deterministic :func:`reviewgate.core.engine.analyze`, and
writes the \u00a710.2-shaped JSON report to stdout.

The CLI is the documented adapter that connects the pure engine to a
shell. The engine itself stays inside the \u00a74.1 boundary (no GitHub,
no network, no filesystem **writes**, no DB, no LLM); the CLI may read
``--input`` from disk and write a JSON report to ``stdout`` because
those are the only side effects required to make the engine usable
locally per \u00a75.1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from .engine import analyze
from .schemas import EngineInput

_PROG: Final[str] = "reviewgate-core"

# Per the \u00a725 M1 acceptance criteria: zero on a successful run, non-zero
# on invalid input. ``2`` is conventional for CLI usage / input errors
# (argparse already uses it for argument parsing failures).
_EXIT_OK: Final[int] = 0
_EXIT_INVALID_INPUT: Final[int] = 2

_STDIN_SENTINEL: Final[str] = "-"
"""Argument value that explicitly forces reading from stdin."""


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser. Pure (no I/O)."""

    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=(
            "Run the deterministic reviewability engine over a fixture "
            "JSON file (DESIGN.md \u00a75.1, \u00a710.1)."
        ),
    )
    parser.add_argument(
        "-i",
        "--input",
        default=None,
        help=(
            "Path to a JSON file matching the \u00a710.1 EngineInput schema. "
            f"Use '{_STDIN_SENTINEL}' or omit to read stdin."
        ),
    )
    return parser


def _read_input(input_arg: str | None) -> str:
    """Load the raw JSON text from a file path or stdin.

    ``None`` and the explicit ``"-"`` sentinel both mean stdin so the
    CLI works in both pipe (``cat fixture.json | reviewgate-core``) and
    explicit (``reviewgate-core --input -``) shells.
    """

    if input_arg is None or input_arg == _STDIN_SENTINEL:
        return sys.stdin.read()
    return Path(input_arg).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``reviewgate-core`` console script.

    Args:
        argv: Optional argv override (omit ``sys.argv[0]``). When
            ``None``, :mod:`argparse` reads ``sys.argv`` itself. This
            argument exists so tests can call :func:`main` directly
            without spawning a subprocess.

    Returns:
        ``0`` on a successful run; ``2`` when the input is not valid
        JSON or fails the \u00a710.1 :class:`EngineInput` schema.
    """

    parser = _build_parser()
    args = parser.parse_args(argv)

    raw = _read_input(args.input)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        print(f"{_PROG}: input is not valid JSON: {err}", file=sys.stderr)
        return _EXIT_INVALID_INPUT

    try:
        engine_input = EngineInput.model_validate(payload)
    except ValidationError as err:
        print(
            f"{_PROG}: input does not match \u00a710.1 EngineInput schema:\n{err}",
            file=sys.stderr,
        )
        return _EXIT_INVALID_INPUT

    report = analyze(engine_input)
    sys.stdout.write(report.model_dump_json(indent=2))
    sys.stdout.write("\n")
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())


__all__ = ["main"]
