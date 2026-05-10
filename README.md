# reviewgate-core

This repository **is** **reviewgate-core**: the open-source, deterministic reviewability engine for ReviewGate, which checks whether a pull request is **reviewable** before humans spend time on it.

Product context, boundaries, and the full stack live in [`docs/DESIGN.md`](docs/DESIGN.md). The Python implementation of the engine lives under `src/reviewgate/core/` (import path `reviewgate.core`; see §15 for module names). The GitHub Action and hosted app are separate codebases described in that document.

## Repository boundary

**Open source and proprietary code do not belong in the same repository.** This tree is only for what will be released as **reviewgate-core** (the deterministic engine, CLI, tests, and related docs). Anything that must stay private—hosted GitHub App backend, LLM integration tied to commercial hosting, billing, org dashboards, and similar—must live in **other repos**, as outlined in `docs/DESIGN.md` §19.2 (commercial repository) and §19.4 (what stays commercial). Do not add proprietary packages, submodules, or “feature flag” shims here to hide closed code; that would prevent a clean public release from this history.

## Development

Python **3.12+** is required (see §15).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## GitHub Action

The open-source GitHub Action wrapper lives in [`reviewgate-action/`](reviewgate-action/).

> **Status: scaffold (issue #23). Do not pin this Action as a required status check yet.** The composite step validates inputs and **exits non-zero** until the runtime lands in issues #24, #25, and #26. The fail-closed posture exists so a workflow that adds the Action to branch protection cannot silently mark a PR mergeable while review logic is still missing.

Reference workflow (verbatim from `docs/DESIGN.md` §14, intended for use once the runtime PRs land):

```yaml
name: ReviewGate

on:
  pull_request:
    types: [opened, synchronize, edited, reopened]

jobs:
  reviewgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: leo-aa88/reviewgate-core/reviewgate-action@v1
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          fail-on: FAIL
          post-comment: true
```

See [`reviewgate-action/README.md`](reviewgate-action/README.md) for the full input/output reference and the §14.1 coexistence rules with the hosted ReviewGate App.

## Contributing

Before opening a PR, see [`CONTRIBUTING.md`](CONTRIBUTING.md). Note in
particular the **`reviewgate-core` purity boundary** (§4.1): the engine
must remain pure (no GitHub API, no network, no filesystem writes, no
database, no LLM, no side effects). CI fails any change that imports a
forbidden module into `src/reviewgate/core/`.
