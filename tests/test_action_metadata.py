"""Contract tests for the reviewgate-action scaffold (issue #23, §14).

The Action's `action.yml` is the public surface consumers reference
from their workflows. These tests lock in the §14 input contract --
required vs optional, default values, and the mode/fail-on/post-comment
enums the runtime branches on -- so a refactor of the scaffold cannot
silently rename an input or change a default. They also assert that
the §14 reference snippet really is reproduced verbatim in the
top-level README and the per-action README, satisfying the second
acceptance criterion of #23.

Pure: no I/O beyond reading text files in the repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_ACTION_YML: Final[Path] = _REPO_ROOT / "reviewgate-action" / "action.yml"
_ACTION_README: Final[Path] = _REPO_ROOT / "reviewgate-action" / "README.md"
_TOP_README: Final[Path] = _REPO_ROOT / "README.md"

# §14 inputs and their default values. Pinned here so a typo or a
# silent rename in `action.yml` fails this test instead of leaking out
# to consumers.
_REQUIRED_INPUTS: Final[frozenset[str]] = frozenset({"github-token"})
_OPTIONAL_INPUT_DEFAULTS: Final[dict[str, str]] = {
    "fail-on": "FAIL",
    "post-comment": "true",
    "mode": "auto",
}
_ALL_INPUTS: Final[frozenset[str]] = (
    _REQUIRED_INPUTS | frozenset(_OPTIONAL_INPUT_DEFAULTS)
)


@pytest.fixture(scope="module")
def action_metadata() -> dict[str, Any]:
    """Parse `reviewgate-action/action.yml` once per test module."""

    raw = _ACTION_YML.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict), (
        f"action.yml must be a YAML mapping, got {type(parsed).__name__}"
    )
    return parsed


def test_action_yml_exists_at_documented_path() -> None:
    """§14 documents the consumer path; the file has to exist there."""

    assert _ACTION_YML.is_file(), (
        f"reviewgate-action/action.yml is missing; consumers reference "
        f"{_ACTION_YML.relative_to(_REPO_ROOT)}"
    )


def test_action_metadata_declares_name_and_description(
    action_metadata: dict[str, Any],
) -> None:
    """Branding requires a non-empty ``name`` and ``description``."""

    assert isinstance(action_metadata.get("name"), str)
    assert action_metadata["name"].strip()
    assert isinstance(action_metadata.get("description"), str)
    assert action_metadata["description"].strip()


def test_action_metadata_inputs_match_design_doc(
    action_metadata: dict[str, Any],
) -> None:
    """All §14 inputs are declared and no surprise inputs leak in.

    Catches drift between `docs/DESIGN.md` §14 and the published Action
    contract: a renamed input here would silently break every consumer
    that copy-pasted the §14 snippet.
    """

    inputs = action_metadata.get("inputs")
    assert isinstance(inputs, dict), "action.yml must declare an `inputs` mapping"
    actual = frozenset(inputs.keys())
    assert actual == _ALL_INPUTS, (
        f"action.yml inputs drifted from §14: missing="
        f"{sorted(_ALL_INPUTS - actual)}, extra={sorted(actual - _ALL_INPUTS)}"
    )


def test_required_inputs_are_marked_required(
    action_metadata: dict[str, Any],
) -> None:
    """`github-token` is the only required input per §14."""

    inputs = action_metadata["inputs"]
    for name in _ALL_INPUTS:
        spec = inputs[name]
        assert isinstance(spec, dict), f"{name}: input spec must be a mapping"
        assert spec.get("description"), f"{name}: input must have a description"
        is_required = bool(spec.get("required", False))
        if name in _REQUIRED_INPUTS:
            assert is_required, f"{name}: must be required per §14"
        else:
            assert not is_required, (
                f"{name}: §14 lists it as optional; required=true would "
                "break the documented snippet"
            )


@pytest.mark.parametrize(
    ("name", "default"),
    sorted(_OPTIONAL_INPUT_DEFAULTS.items()),
)
def test_optional_inputs_have_design_doc_defaults(
    action_metadata: dict[str, Any], name: str, default: str
) -> None:
    """Default values must match the §14 snippet exactly.

    YAML scalar coercion turns `default: true` into a Python ``True``,
    but GitHub Actions hands every input value to the runner as a
    string. We therefore quote booleans in `action.yml`
    (``default: "true"``) and assert string equality here.
    """

    inputs = action_metadata["inputs"]
    actual = inputs[name].get("default")
    assert isinstance(actual, str), (
        f"{name}: default must be a string (got {type(actual).__name__}); "
        "quote boolean defaults in action.yml so consumers receive the "
        "exact string GitHub Actions delivers."
    )
    assert actual == default, (
        f"{name}: default drifted from §14 (expected {default!r}, got {actual!r})"
    )


def test_action_runs_uses_composite_per_design_doc(
    action_metadata: dict[str, Any],
) -> None:
    """§14 keeps the Action in a single repo (no Docker, no Node).

    Composite is the only `using` value that lets a YAML-defined Action
    invoke shell + Python without a separate runtime image; this test
    locks that decision in so a refactor cannot accidentally turn the
    Action into a Docker action and add a build step to every consumer.
    """

    runs = action_metadata.get("runs")
    assert isinstance(runs, dict), "action.yml must declare a `runs` mapping"
    assert runs.get("using") == "composite", (
        f"runs.using must be 'composite' per §14; got {runs.get('using')!r}"
    )
    steps = runs.get("steps")
    assert isinstance(steps, list) and steps, (
        "runs.steps must be a non-empty list (scaffold step counts)"
    )


def test_scaffold_step_fails_closed_until_runtime_lands(
    action_metadata: dict[str, Any],
) -> None:
    """The scaffold's composite step must exit non-zero (fail-closed).

    Branch-protection safety guard: a workflow that names this Action
    as a required status check on a PR must not be able to mark that
    PR mergeable while the review runtime is still missing. Until
    #24-#26 land, the scaffold step has to terminate with a clear
    ``::error::`` and ``exit 1``. This test asserts the step's shell
    body still contains both markers; if a future change re-enables
    the no-op success path before the runtime is wired, the suite
    fails before that change can ship.
    """

    steps = action_metadata["runs"]["steps"]
    scaffold_step = steps[0]
    assert isinstance(scaffold_step, dict)
    body = scaffold_step.get("run")
    assert isinstance(body, str), "scaffold step must have a shell `run` body"

    assert "::error::" in body, (
        "scaffold must emit an ::error:: line so the failure is visible "
        "in PR check summaries"
    )
    assert "exit 1" in body, (
        "scaffold must `exit 1` to fail the workflow check (fail-closed) "
        "until #24-#26 wire the runtime; otherwise consumers that pin "
        "this Action as a required check could silently merge without "
        "review."
    )


def test_action_readme_warns_scaffold_is_not_runnable() -> None:
    """Both READMEs must warn the scaffold is not runnable yet.

    Drift catcher: the §14 snippet remains in both READMEs as
    documentation, but the warning above it is what tells a copy-paste
    adopter not to wire the scaffold into branch protection. Removing
    the warning while leaving the snippet would invite the exact
    silent-bypass risk the fail-closed step exists to prevent.
    """

    for path in (_TOP_README, _ACTION_README):
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        assert "scaffold" in lower, (
            f"{path.name} must mention the scaffold status"
        )
        assert "do not pin" in lower or "not runnable" in lower, (
            f"{path.name} must warn that the scaffold is not safe to pin "
            "as a required status check yet"
        )


def test_scaffold_does_not_declare_unimplemented_outputs(
    action_metadata: dict[str, Any],
) -> None:
    """Outputs are intentionally absent until the runtime can fill them.

    Composite outputs must reference a real step output. Emitting empty
    placeholders for `reviewability` / `report-json` would silently
    break ``if: steps.x.outputs.reviewability == 'PASS'`` checks and
    crash ``fromJSON(steps.x.outputs.report-json)`` consumers. Both
    outputs are wired in #25 alongside the core runtime; this guard
    fails if a future change reintroduces an empty-string output
    before the runtime is ready.
    """

    outputs = action_metadata.get("outputs")
    if outputs is None:
        return
    assert isinstance(outputs, dict)
    for name, spec in outputs.items():
        assert isinstance(spec, dict), f"{name}: output spec must be a mapping"
        value = spec.get("value")
        assert isinstance(value, str) and value.strip(), (
            f"{name}: declared output must be wired to a non-empty step "
            "expression; do not declare outputs that resolve to an empty "
            "string -- that breaks fromJSON() and equality checks for "
            "consumers."
        )


def test_top_level_readme_includes_design_doc_snippet() -> None:
    """The §14 reference snippet must be reproducible from the README."""

    readme = _TOP_README.read_text(encoding="utf-8")
    assert "leo-aa88/reviewgate-core/reviewgate-action@v1" in readme, (
        "Top-level README must reference the Action at its consumer path"
    )
    assert "github-token: ${{ secrets.GITHUB_TOKEN }}" in readme
    assert "fail-on: FAIL" in readme
    assert "post-comment: true" in readme


def test_action_readme_documents_every_input_and_planned_outputs() -> None:
    """The per-action README must list every input and the planned outputs.

    Drift catcher: if a future PR adds an input to `action.yml` but
    forgets the docs row, this test fails before the change ships.
    The two §14 outputs (`reviewability`, `report-json`) are
    documented as "planned" in the scaffold (they land with the
    runtime in #25) but the names must still appear so consumers
    reading the README know what the eventual contract is.
    """

    readme = _ACTION_README.read_text(encoding="utf-8")
    for name in _ALL_INPUTS:
        assert f"`{name}`" in readme, (
            f"reviewgate-action/README.md must document input `{name}`"
        )
    for name in ("reviewability", "report-json"):
        assert f"`{name}`" in readme, (
            f"reviewgate-action/README.md must document planned output `{name}`"
        )
