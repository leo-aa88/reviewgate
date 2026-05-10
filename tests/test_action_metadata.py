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


def test_action_outputs_expose_reviewability_and_report(
    action_metadata: dict[str, Any],
) -> None:
    """The §10.13 verdict and §10.2 report JSON are public outputs.

    Consumers chain follow-up jobs on the verdict (e.g. label apply,
    Slack notify); locking these output names prevents a rename from
    breaking downstream `needs.<job>.outputs.reviewability` lookups.
    """

    outputs = action_metadata.get("outputs")
    assert isinstance(outputs, dict), "action.yml must declare an `outputs` mapping"
    assert {"reviewability", "report-json"} <= set(outputs.keys()), (
        f"outputs missing required keys; got {sorted(outputs.keys())}"
    )
    for name in ("reviewability", "report-json"):
        spec = outputs[name]
        assert isinstance(spec, dict)
        assert spec.get("description"), f"{name}: output must have a description"
        assert spec.get("value"), (
            f"{name}: composite outputs must declare a `value` mapping to "
            "a step output"
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


def test_action_readme_documents_every_input_and_output() -> None:
    """The per-action README must list every input and output by name.

    Drift catcher: if a future PR adds an input to `action.yml` but
    forgets the docs row, this test fails before the change ships.
    """

    readme = _ACTION_README.read_text(encoding="utf-8")
    for name in _ALL_INPUTS:
        assert f"`{name}`" in readme, (
            f"reviewgate-action/README.md must document input `{name}`"
        )
    for name in ("reviewability", "report-json"):
        assert f"`{name}`" in readme, (
            f"reviewgate-action/README.md must document output `{name}`"
        )
