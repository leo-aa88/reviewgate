"""Deterministic engine entry point (docs/DESIGN.md \u00a710).

This module owns the public ``analyze`` function that the CLI, the
GitHub Action, and the hosted App worker all call. It is the documented
boundary of \u00a74.1: pure, no I/O, no GitHub or LLM dependencies, and a
stable signature ``EngineInput -> ReviewabilityReport``.

Milestone 1 ships a stub: it routes verdict through
:func:`reviewgate.core.aggregate.baseline_reviewability` over an empty
warning set (so the result is always ``PASS``) and reports raw-size
statistics directly from the input. The Milestone 2 issues (#15\u2013#16)
plug in the file categorizer, human-LOC calculator, and individual
warning emitters; the function signature does not change.
"""

from __future__ import annotations

from .aggregate import baseline_reviewability
from .schemas import EngineInput, EngineWarning, ReviewabilityReport


def analyze(engine_input: EngineInput) -> ReviewabilityReport:
    """Run the deterministic engine over a normalized PR input (\u00a710).

    Args:
        engine_input: A validated :class:`EngineInput` matching the
            \u00a710.1 schema. Validation is the caller's responsibility
            (the CLI does it via :class:`pydantic.BaseModel.model_validate`).

    Returns:
        A :class:`ReviewabilityReport` matching the \u00a710.2 schema.
        Stub semantics:

        * ``reviewability``: result of
          :func:`baseline_reviewability` on the (currently empty) warning
          list, i.e. ``"PASS"``.
        * ``stats``: ``files_changed``, ``raw_loc_changed``,
          ``additions``, and ``deletions`` taken straight from
          :attr:`EngineInput.pr`.
        * Every list field defaults to ``[]``; Milestone 2 fills them
          in without changing the report shape.
    """

    pr = engine_input.pr
    warnings: list[EngineWarning] = []
    return ReviewabilityReport(
        reviewability=baseline_reviewability(warnings),
        stats={
            "files_changed": pr.changed_files,
            "raw_loc_changed": pr.additions + pr.deletions,
            "additions": pr.additions,
            "deletions": pr.deletions,
        },
        warnings=warnings,
    )


__all__ = ["analyze"]
