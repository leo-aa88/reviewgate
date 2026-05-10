"""§14.1 coexistence rules between the Action and the hosted App.

The Action's ``mode`` input and ``.reviewgate.yml``'s ``mode`` field
together decide who owns the §13 PR-comment surface (hosted App,
Action, or both) and whether the Action's workflow result should fail
the PR check or stay quiet. This module is the single source of truth
for that resolution; both :mod:`reviewgate_action.run_core` and the
test suite call into :func:`decide` rather than re-encoding the §14.1
table.

Rules implemented (verbatim from §14.1):

============= ================== ==========================================
Action ``mode``  Config ``mode``     Action behaviour
============= ================== ==========================================
``quiet``       (any)              Stay silent. Run the engine for the
                                   workflow log + summary, but never post a
                                   comment and never apply ``fail-on`` (the
                                   Action exits 0 regardless of the verdict).
``action``      (any)              Action owns posting. ``post-comment``
                                   gates the actual upsert; ``fail-on``
                                   policy applies normally.
``auto``        ``app``            Hosted App owns posting -- Action stays
                                   quiet (no comment, no failing exit).
``auto``        ``action``         Action posts; hosted App is expected to
                                   skip on its side.
``auto``        ``both``           Action posts; hosted App also posts. The
                                   §13 marker keeps each side's comment
                                   distinct so neither edits the other.
============= ================== ==========================================

Pure: no I/O. The decision depends only on the two modes and the
boolean ``post-comment`` flag the workflow author set; the caller
takes care of fetching `.reviewgate.yml` and the report.
"""

from __future__ import annotations

from typing import Final, Literal, NamedTuple

ActionMode = Literal["auto", "action", "quiet"]
"""`mode` input enum from §14 / `action.yml`."""

ConfigMode = Literal["app", "action", "both"]
"""`mode` field enum from §12 (`.reviewgate.yml`)."""


class CoexistenceDecision(NamedTuple):
    """Outcome of :func:`decide`.

    Attributes:
        post_comment: Whether the Action should call the §13 PR-comment
            upsert path. ``False`` either because mode-coexistence
            forbids posting or because the workflow author passed
            ``post-comment: false``.
        apply_fail_on: Whether the §14 ``fail-on`` policy should drive
            the workflow exit code. ``False`` means the Action stays
            quiet (`mode: quiet` or `auto`+`mode: app`) and the run
            always exits 0 even on a FAIL verdict.
        rationale: Single-line, human-readable explanation suitable
            for the workflow log so an operator can reproduce the
            decision without re-reading §14.1.
    """

    post_comment: bool
    apply_fail_on: bool
    rationale: str


_QUIET_RATIONALE: Final[str] = (
    "Action `mode: quiet` -- engine ran for the workflow summary, but "
    "no comment is posted and `fail-on` is ignored."
)
_ACTION_OVERRIDE_RATIONALE: Final[str] = (
    "Action `mode: action` -- Action owns the §13 comment surface "
    "regardless of `.reviewgate.yml`."
)
_AUTO_APP_RATIONALE: Final[str] = (
    "Action `mode: auto` and `.reviewgate.yml` `mode: app` -- hosted "
    "App owns posting per §14.1; Action stays quiet."
)
_AUTO_ACTION_RATIONALE: Final[str] = (
    "Action `mode: auto` and `.reviewgate.yml` `mode: action` -- "
    "hosted App is expected to skip; Action posts the §13 comment."
)
_AUTO_BOTH_RATIONALE: Final[str] = (
    "Action `mode: auto` and `.reviewgate.yml` `mode: both` -- both "
    "surfaces post; the §13 marker keeps the comments distinct."
)


def decide(
    *,
    action_mode: ActionMode,
    config_mode: ConfigMode,
    post_comment_input: bool,
) -> CoexistenceDecision:
    """Apply the §14.1 coexistence table.

    Args:
        action_mode: The Action's ``mode`` input value (``auto`` /
            ``action`` / ``quiet``). Validation against the literal
            enum is the caller's responsibility (the composite step's
            bash prelude already does this for ``action.yml``
            consumers; tests that bypass argparse should validate
            their own inputs).
        config_mode: The effective ``mode`` field from
            ``.reviewgate.yml`` (``app`` / ``action`` / ``both``).
            When the file is missing or malformed, callers should
            pass the §12 default (``"app"``) so the Action stays
            silent by default; this matches the
            ``ReviewGateConfig().mode`` default and avoids a
            surprise comment on first install.
        post_comment_input: The Action's ``post-comment`` input.
            ``False`` is an explicit opt-out from the workflow
            author; the function honours it even when coexistence
            would otherwise allow posting.

    Returns:
        The :class:`CoexistenceDecision` whose ``post_comment`` and
        ``apply_fail_on`` flags drive the rest of the run, plus a
        ``rationale`` string for the workflow log.
    """

    if action_mode == "quiet":
        return CoexistenceDecision(False, False, _QUIET_RATIONALE)

    if action_mode == "action":
        return CoexistenceDecision(
            post_comment=post_comment_input,
            apply_fail_on=True,
            rationale=_ACTION_OVERRIDE_RATIONALE,
        )

    if config_mode == "app":
        return CoexistenceDecision(False, False, _AUTO_APP_RATIONALE)

    rationale = (
        _AUTO_ACTION_RATIONALE if config_mode == "action" else _AUTO_BOTH_RATIONALE
    )
    return CoexistenceDecision(
        post_comment=post_comment_input,
        apply_fail_on=True,
        rationale=rationale,
    )


__all__ = [
    "ActionMode",
    "CoexistenceDecision",
    "ConfigMode",
    "decide",
]
