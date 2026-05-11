"""Contract tests for the reviewgate-action runtime (issue #25, §14).

The Action's `action.yml` is the public surface consumers reference
from their workflows. These tests lock in the §14 input contract --
required vs optional, default values, and the mode/fail-on/post-comment
enums the runtime branches on -- so a refactor cannot silently rename
an input or change a default. They also assert that the §14 reference
snippet really is reproduced verbatim in the top-level README and the
per-action README.

After #25 the Action carries real runtime steps (setup-python, install,
fetch, run_core); these tests pin the composite shape and exercise the
input-validation enum branches end to end without invoking the GitHub
API. The pure runtime behaviour itself is covered by
:mod:`tests.test_run_core` and :mod:`tests.test_fetch_pr`.

Pure: no I/O beyond reading text files in the repo and running a small
bash script in a temp dir.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_ACTION_YML: Final[Path] = (
    _REPO_ROOT / "src" / "reviewgate_action" / "action.yml"
)
_ACTION_README: Final[Path] = (
    _REPO_ROOT / "src" / "reviewgate_action" / "README.md"
)
_TOP_README: Final[Path] = _REPO_ROOT / "README.md"

# §14 inputs and their default values. Pinned here so a typo or a
# silent rename in `action.yml` fails this test instead of leaking out
# to consumers. `python-version` and `working-directory` were added in
# #25 to expose the setup-python pin and the workspace root override
# respectively; both are optional with sensible defaults.
_REQUIRED_INPUTS: Final[frozenset[str]] = frozenset({"github-token"})
_OPTIONAL_INPUT_DEFAULTS: Final[dict[str, str]] = {
    "fail-on": "FAIL",
    "post-comment": "true",
    "mode": "auto",
    "python-version": "3.12",
    "working-directory": "",
}
_ALL_INPUTS: Final[frozenset[str]] = (
    _REQUIRED_INPUTS | frozenset(_OPTIONAL_INPUT_DEFAULTS)
)

_DECLARED_OUTPUTS: Final[frozenset[str]] = frozenset({"reviewability", "report-json"})


@pytest.fixture(scope="module")
def action_metadata() -> dict[str, Any]:
    """Parse `src/reviewgate_action/action.yml` once per test module."""

    raw = _ACTION_YML.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict), (
        f"action.yml must be a YAML mapping, got {type(parsed).__name__}"
    )
    return parsed


def test_action_yml_exists_at_documented_path() -> None:
    """§14 documents the consumer path; the file has to exist there."""

    assert _ACTION_YML.is_file(), (
        f"src/reviewgate_action/action.yml is missing; consumers reference "
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
    """All §14 inputs are declared and no surprise inputs leak in."""

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
    invoke shell + Python without a separate runtime image.
    """

    runs = action_metadata.get("runs")
    assert isinstance(runs, dict), "action.yml must declare a `runs` mapping"
    assert runs.get("using") == "composite", (
        f"runs.using must be 'composite' per §14; got {runs.get('using')!r}"
    )
    steps = runs.get("steps")
    assert isinstance(steps, list) and steps, (
        "runs.steps must be a non-empty list"
    )


def test_action_runs_pins_python_via_setup_python(
    action_metadata: dict[str, Any],
) -> None:
    """The runtime requires Python 3.12+ (§15); pin via setup-python.

    Without an explicit setup-python step, a self-hosted runner could
    expose an older Python and the engine would fail to import on
    PEP 604 unions. Pinning via `actions/setup-python@v5` matches the
    §15 stack and gives consumers a single knob (`python-version`) to
    upgrade.
    """

    steps = action_metadata["runs"]["steps"]
    setup_steps = [
        s for s in steps
        if isinstance(s, dict) and isinstance(s.get("uses"), str)
        and s["uses"].startswith("actions/setup-python@")
    ]
    assert setup_steps, (
        "composite must include `actions/setup-python@v5` so the engine "
        "runs on the §15-pinned interpreter"
    )
    spec = setup_steps[0]
    with_block = spec.get("with")
    assert isinstance(with_block, dict)
    pin = with_block.get("python-version")
    assert isinstance(pin, str) and "${{ inputs.python-version }}" in pin, (
        "setup-python must read its version from the `python-version` "
        f"input so consumers can override the default; got {pin!r}"
    )


def test_action_declares_runtime_outputs(
    action_metadata: dict[str, Any],
) -> None:
    """The §14 outputs (`reviewability`, `report-json`) must be wired.

    Composite outputs must reference a real step output expression.
    An empty-string `value:` would silently break
    ``if: steps.x.outputs.reviewability == 'PASS'`` checks and crash
    ``fromJSON(steps.x.outputs.report-json)`` consumers, so this test
    asserts the references are non-empty *and* point at the run step
    that produces them.
    """

    outputs = action_metadata.get("outputs")
    assert isinstance(outputs, dict), (
        "action.yml must declare the §14 `outputs` block now that the "
        "runtime can fill it (#25)"
    )
    assert frozenset(outputs.keys()) == _DECLARED_OUTPUTS, (
        f"outputs drifted from §14: got {sorted(outputs)}, "
        f"expected {sorted(_DECLARED_OUTPUTS)}"
    )
    for name, spec in outputs.items():
        assert isinstance(spec, dict), f"{name}: output spec must be a mapping"
        value = spec.get("value")
        assert isinstance(value, str) and value.strip(), (
            f"{name}: output `value:` must be a non-empty step expression"
        )
        assert "steps.run.outputs" in value, (
            f"{name}: output must reference the `run` step; got {value!r}"
        )


def test_top_level_readme_includes_design_doc_snippet() -> None:
    """The §14 reference snippet must be reproducible from the README.

    The ``uses:`` value uses GitHub Actions' documented
    ``{owner}/{repo}/{path}@{ref}`` form so consumers can reference
    the Action at its real subdirectory location
    (``src/reviewgate_action/action.yml``). This is *not* a typo of the
    ``{owner}/{repo}@{ref}`` form -- the official "Using actions"
    docs cover both shapes, and the path form survives the future
    split into the standalone `reviewgate/reviewgate-action` repo.
    """

    readme = _TOP_README.read_text(encoding="utf-8")
    assert "leo-aa88/reviewgate/src/reviewgate_action@main" in readme, (
        "Top-level README must reference the Action at its subdirectory "
        "consumer path (DESIGN.md §14, GitHub Actions "
        "{owner}/{repo}/{path}@{ref} form)"
    )
    assert "github-token: ${{ secrets.GITHUB_TOKEN }}" in readme
    assert "fail-on: FAIL" in readme
    assert "post-comment: true" in readme


_FENCED_USES_RE: Final[re.Pattern[str]] = re.compile(
    r"```yaml\n(?P<body>.*?)\n```",
    re.DOTALL,
)
_USES_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*-\s+uses:\s+(?P<ref>\S+)\s*$",
    re.MULTILINE,
)


def _uses_refs_in_yaml_blocks(readme_text: str) -> list[str]:
    """Return every ``- uses: …`` value inside a fenced ```yaml block."""

    refs: list[str] = []
    for block in _FENCED_USES_RE.finditer(readme_text):
        for match in _USES_LINE_RE.finditer(block.group("body")):
            refs.append(match.group("ref"))
    return refs


def test_action_subdirectory_path_in_uses_resolves_to_real_action_yml() -> None:
    """The README's ``uses:`` subdirectory path must hit a real ``action.yml``.

    Concretely: extract every ``- uses:`` value inside a fenced
    ```yaml block in the top-level README, find the
    ``leo-aa88/reviewgate/<path>@<ref>`` reference, and assert
    ``<repo>/<path>/action.yml`` exists on disk. Catches the
    foot-gun of documenting a path that does not match the actual
    repository layout.
    """

    readme = _TOP_README.read_text(encoding="utf-8")
    refs = _uses_refs_in_yaml_blocks(readme)
    assert refs, "README must contain at least one fenced YAML `uses:` line"

    repo_prefix = "leo-aa88/reviewgate/"
    matching = [r for r in refs if r.startswith(repo_prefix)]
    assert matching, (
        f"README's fenced YAML must reference the Action via "
        f"`uses: {repo_prefix}<path>@<ref>`; got refs={refs}"
    )
    for ref in matching:
        path_segment = ref[len(repo_prefix) :].split("@", 1)[0].strip()
        assert path_segment, f"empty subdirectory path in `uses: {ref}`"
        resolved = _REPO_ROOT / path_segment / "action.yml"
        assert resolved.is_file(), (
            f"README documents `uses: {ref}` but "
            f"{resolved.relative_to(_REPO_ROOT)} does not exist"
        )
        assert resolved == _ACTION_YML, (
            f"documented `uses: {ref}` resolves to "
            f"{resolved.relative_to(_REPO_ROOT)}, but the test fixture "
            f"expects {_ACTION_YML.relative_to(_REPO_ROOT)}"
        )


# --- shell validation branches (composite step is bash, so the input ----
# enum branches are exercised by running the same script body the Action
# would run, under the same env-var contract.

def _run_step_body() -> str:
    """Extract the run step's ``run:`` shell body from action.yml.

    Pulled out of the YAML so the shell tests stay in lockstep with
    the actual Action behaviour: any change to the validation
    branches in `action.yml` is reflected here automatically.
    """

    metadata = yaml.safe_load(_ACTION_YML.read_text(encoding="utf-8"))
    run_step = next(
        (
            s
            for s in metadata["runs"]["steps"]
            if isinstance(s, dict) and s.get("id") == "run"
        ),
        None,
    )
    assert run_step is not None, "composite must have a step with id `run`"
    body = run_step.get("run")
    assert isinstance(body, str) and body.strip()
    return body


def _run_validation_only(
    *,
    fail_on: str,
    post_comment: str,
    mode: str,
    tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run the validation prelude of the run step's bash body.

    The full run body shells out to ``python -m
    reviewgate_action.fetch_pr`` and ``run_core`` after the input
    validation case-statements; running that here would require a
    real GitHub token and network access. Instead, we slice the body
    at the first ``python -m`` invocation, keeping only the
    validation prelude, and execute that. The slice keeps the case
    statements behaviour-compatible with what GitHub Actions runs
    while staying hermetic.
    """

    body = _run_step_body()
    cut_marker = "workdir="
    assert cut_marker in body, (
        "run step body must contain the post-validation `workdir=` line; "
        "if this assert fires the prelude has been refactored and the "
        "test slice needs to be updated"
    )
    prelude = body.split(cut_marker, 1)[0]

    script = tmp_path / "validate.sh"
    script.write_text(prelude, encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")

    return subprocess.run(  # noqa: S603 - intentional shell-out, fixed args
        ["bash", str(script)],
        env={
            "REVIEWGATE_FAIL_ON": fail_on,
            "REVIEWGATE_POST_COMMENT": post_comment,
            "REVIEWGATE_MODE": mode,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_WORKSPACE": str(tmp_path),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_run_step_validation_accepts_documented_inputs(tmp_path: Path) -> None:
    """All §14 documented inputs must pass the prelude validation.

    The prelude (case-statements) is the public input contract;
    documented values must not trigger the ``exit 2`` branch.
    """

    proc = _run_validation_only(
        fail_on="FAIL",
        post_comment="true",
        mode="auto",
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0, (
        f"valid inputs must pass validation; got returncode={proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )


@pytest.mark.parametrize(
    ("fail_on", "post_comment", "mode", "expected_substring"),
    [
        ("MAYBE", "true", "auto", "fail-on must be one of"),
        ("FAIL", "yes", "auto", "post-comment must be 'true' or 'false'"),
        ("FAIL", "true", "loud", "mode must be one of"),
        ("", "true", "auto", "fail-on must be one of"),
    ],
)
def test_run_step_validation_rejects_invalid_inputs(
    tmp_path: Path,
    fail_on: str,
    post_comment: str,
    mode: str,
    expected_substring: str,
) -> None:
    """Invalid input enums must fail with exit 2 + diagnostic ``::error::``.

    Exercises the three documented enum contracts end to end (not
    just by string-grepping the YAML) so the public input contract
    cannot silently regress.
    """

    proc = _run_validation_only(
        fail_on=fail_on,
        post_comment=post_comment,
        mode=mode,
        tmp_path=tmp_path,
    )
    assert proc.returncode == 2, (
        f"invalid input must exit 2; got returncode={proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert expected_substring in proc.stdout, (
        f"expected diagnostic containing {expected_substring!r} in stdout; "
        f"got {proc.stdout!r}"
    )


def test_run_step_falls_back_to_empty_json_object_for_report(
    action_metadata: dict[str, Any],
) -> None:
    """The `report-json` output must always be valid JSON.

    Consumers commonly write
    ``${{ fromJSON(steps.x.outputs.report-json) }}`` in workflow
    expressions; an empty-string fallback would crash that
    expression at evaluation time. The run step must therefore
    write at least ``{}`` on every failure path so ``fromJSON``
    keeps working. This test asserts the bash body still publishes
    the documented fallback.
    """

    body = _run_step_body()
    assert "report-json={}" in body, (
        "run step must fall back to `report-json={}` (empty JSON object) "
        "when the engine never produced a report; an empty string would "
        "crash `fromJSON()` for consumers reading the output"
    )


def test_action_readme_documents_every_input_and_output() -> None:
    """The per-action README must list every input and the §14 outputs."""

    readme = _ACTION_README.read_text(encoding="utf-8")
    for name in _ALL_INPUTS:
        assert f"`{name}`" in readme, (
            f"src/reviewgate_action/README.md must document input `{name}`"
        )
    for name in _DECLARED_OUTPUTS:
        assert f"`{name}`" in readme, (
            f"src/reviewgate_action/README.md must document output `{name}`"
        )
