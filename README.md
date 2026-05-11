# ReviewGate

> Make pull requests reviewable before humans waste time on them.

[![CI](https://github.com/leo-aa88/reviewgate/actions/workflows/ci.yml/badge.svg)](https://github.com/leo-aa88/reviewgate/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-brightgreen.svg)](#testing)
[![Status: beta](https://img.shields.io/badge/status-beta-orange.svg)](#status)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://www.conventionalcommits.org)

ReviewGate is a deterministic **pull-request intake gate**. It checks
whether a PR is *reviewable* (size, scope, missing context, risky
paths, splitability) before humans spend time on it. It does **not**
review code correctness, security, or merge safety — that's a
deliberate, narrow scope. See [`docs/DESIGN.md`](docs/DESIGN.md) for
the full design and product thesis.

This repository is the **open-source** home for ReviewGate under
Apache 2.0:

* [`reviewgate-core`](src/reviewgate/core/) — the deterministic
  reviewability engine (`reviewgate.core`, pure Python, no I/O).
* [`src/reviewgate_action/`](src/reviewgate_action/) — the GitHub Action
  wrapper that runs the engine on every PR.

The **hosted GitHub App** and **LLM-augmented report layer** are also
open source in this repository under [`src/reviewgate/app/`](src/reviewgate/app/).
The public PR URL analyzer remains future work, tracked openly on the
[issue tracker](https://github.com/leo-aa88/reviewgate/issues); any
future packaging split is for deployment only, not a proprietary fork.
See [`docs/DESIGN.md` §19](docs/DESIGN.md).

---

## Table of contents

- [Why ReviewGate?](#why-reviewgate)
- [What ReviewGate is — and is not](#what-reviewgate-is--and-is-not)
- [Quickstart (5 minutes)](#quickstart-5-minutes)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [The deterministic engine](#the-deterministic-engine)
- [GitHub Action](#github-action)
- [CLI usage](#cli-usage)
- [Onboarding](#onboarding)
- [Docker image](#docker-image)
- [Hosted stack (local)](docs/HOSTED_LOCAL.md)
- [Makefile (local development)](#makefile-local-development)
- [Project layout](#project-layout)
- [Status](#status)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Governance](GOVERNANCE.md)
- [Security](#security)
- [License](#license)
- [Acknowledgements](#acknowledgements)

---

## Why ReviewGate?

AI-assisted coding raised PR volume. It did not raise human review
capacity. Teams now see more PRs, larger diffs, weaker descriptions,
mixed concerns, and review fatigue.

ReviewGate doesn't try to compete with AI code review. It owns the
step *before* review:

> **Is this PR shaped well enough for a human reviewer to spend time
> on it?**

A senior engineer may already know a PR is unreviewable, but saying
so manually creates social friction. ReviewGate turns subjective
reviewer frustration into neutral workflow enforcement.

```text
PR opened or updated
→ ReviewGate analyzes reviewability
→ comment + labels + status check
→ author fixes PR before reviewers waste time
```

---

## What ReviewGate is — and is not

| ReviewGate **is** | ReviewGate **is not** |
| --- | --- |
| A PR intake checker | An AI code reviewer |
| A reviewability gate | A security or vulnerability scanner |
| Pre-review quality tooling | A bug finder |
| Reviewer-time protection | A Copilot replacement |
| A GitHub workflow enforcement tool | A CI optimizer or a linter for code style |
| An open-source rules engine with optional hosted enforcement | An "AI-origin" or "AI slop" detector |

**Language rule.** ReviewGate does not accuse authors of using AI and
does not label PRs as AI-generated. It evaluates observable PR shape
only — it should never care whether a PR came from a human, Copilot,
Cursor, Claude, Devin, or an internal agent.

---

## Quickstart (5 minutes)

The full step-by-step is in [`docs/QUICKSTART.md`](docs/QUICKSTART.md).
The short version:

```yaml
# .github/workflows/reviewgate.yml
name: ReviewGate

on:
  pull_request:
    types: [opened, synchronize, edited, reopened]

jobs:
  reviewgate:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      # Pre-release docs use @main until the first public tag is cut.
      # After v0.1.0, pin a release tag instead.
      - uses: leo-aa88/reviewgate/src/reviewgate_action@main
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          fail-on: FAIL
          post-comment: true
          mode: action
```

`mode: action` makes the Action own comments and `fail-on` enforcement;
the hosted App uses the `.reviewgate.yml` default `mode: app` instead.
That workflow gives you the §10 deterministic verdict on every PR, a
Markdown summary in the workflow run, and a single PR comment that
updates in place on each push (using the `<!-- reviewgate-report -->`
marker). To make the gate **block merges**, mark the workflow as a
required status check in branch protection (Settings → Branches).
[`docs/QUICKSTART.md`](docs/QUICKSTART.md) walks through that and the
recommended `.reviewgate.yml` starter.

---

## How it works

```text
                   ┌─────────────────────────────────────────────────┐
                   │  open-source `reviewgate-core` (this repo)       │
                   │                                                 │
   GitHub PR ───►  │  EngineInput  ──►  analyze()  ──►  Reviewability │
                   │  (§10.1 JSON)        │            Report (§10.2) │
                   │                       └── pure, no I/O (§4.1)   │
                   └────────────────────────────┬────────────────────┘
                                                │
              ┌─────────────────────────────────┼─────────────────────────────┐
              │                                 │                             │
   ┌──────────▼───────────┐         ┌───────────▼────────────┐   ┌────────────▼───────────┐
   │  reviewgate-action   │         │  Hosted ReviewGate App │   │  Local CLI             │
   │  (this repo)         │         │  (this repo; app extra)│   │  reviewgate-core       │
   │  GitHub Action       │         │  webhooks + LLM layer  │   │  → fixture JSON in     │
   │  fetches PR, runs    │         │  + status check        │   │    report JSON out     │
   │  engine, comments,   │         │  (§4.3, §11)           │   │  (§5.1)                │
   │  applies fail-on     │         │                        │   │                        │
   │  policy (§14)        │         │                        │   │                        │
   └──────────────────────┘         └────────────────────────┘   └────────────────────────┘
```

The deterministic engine has a **hard purity boundary** (§4.1):

* no GitHub API calls,
* no network I/O,
* no filesystem writes,
* no database access,
* no LLM calls,
* no comments, labels, or status checks,
* no side effects.

CI enforces the boundary via [`tests/test_core_purity.py`](tests/test_core_purity.py),
which parses every `.py` file under `src/reviewgate/core/` with `ast`
and fails any change that imports a forbidden module. The same test
asserts `pyproject.toml` does not pull a forbidden runtime dependency.

---

## Configuration

Drop a `.reviewgate.yml` at the repo root on the default branch. Every
key has a documented default; an empty file is valid.

```yaml
version: 1
mode: app                           # §14.1 coexistence: app | action | both
llm_reports: false                  # §21.3 — hosted-App-only, opt-in

thresholds:                          # §10.3
  warn:
    files_changed: 25
    human_loc_changed: 800
  fail:
    files_changed: 75
    human_loc_changed: 2500

policy:                              # §10.10
  require_linked_issue: true
  require_human_summary: true
  fail_on_risky_paths_without_context: true

risky_paths:                         # §10.6 — defaults already cover migrations,
  - "**/migrations/**"               #         auth, billing, payments, infra,
  - "infra/**"                       #         terraform, .github/workflows.
  - "src/payments/**"

labels:                              # §13.9 — applied by the hosted App
  pass: reviewability-pass
  warn: reviewability-warn
  fail: reviewability-fail

status_check:                        # §13.10
  name: reviewgate/reviewability
  fail_on: FAIL
```

Strict by design:

* Unknown top-level keys fail validation with the offending key name.
* A malformed file never crashes analysis (§12). The engine emits a
  `config_invalid` warning and runs against defaults so you don't get
  a green CI on a typo by accident.

---

## The deterministic engine

Implemented in [`src/reviewgate/core/`](src/reviewgate/core/). Every
module ties back to a §-numbered section of `docs/DESIGN.md`:

| Module | Purpose | Design § |
| ------ | ------- | -------- |
| [`engine.py`](src/reviewgate/core/engine.py) | Public entry point: `analyze(EngineInput) -> ReviewabilityReport` | §4.1, §10 |
| [`schemas.py`](src/reviewgate/core/schemas.py) | Strict Pydantic models for §10.1 input and §10.2 output | §10.1, §10.2 |
| [`config.py`](src/reviewgate/core/config.py) | `.reviewgate.yml` schema, defaults, malformed-config recovery | §12 |
| [`paths.py`](src/reviewgate/core/paths.py) | Pure gitignore-style glob matcher | §10.6–§10.9 |
| [`categorizer.py`](src/reviewgate/core/categorizer.py) | Per-file categorization across 16 closed labels | §10.5 |
| [`size.py`](src/reviewgate/core/size.py) | Raw + human-authored LOC and size warnings | §10.3, §10.4 |
| [`ignored_paths.py`](src/reviewgate/core/ignored_paths.py) | Applies `ignored_paths` before categorisation | §12 |
| [`count_warnings.py`](src/reviewgate/core/count_warnings.py) | Warn-tier risky / dependency / config file counts | §10.3 |
| [`tests_coverage.py`](src/reviewgate/core/tests_coverage.py) | Source changes without test files (bounded heuristic) | §9, §13.9 |
| [`pr_body.py`](src/reviewgate/core/pr_body.py) | Weak-PR-body detection | §10.10 |
| [`linked_issue.py`](src/reviewgate/core/linked_issue.py) | Linked-issue / ticket reference detection | §10.10 |
| [`risky_paths.py`](src/reviewgate/core/risky_paths.py) | Risky-paths-without-rationale heuristic | §10.6, §10.10 |
| [`mixed_concern.py`](src/reviewgate/core/mixed_concern.py) | Mixed-concern category clusters | §10.11 |
| [`aggregate.py`](src/reviewgate/core/aggregate.py) | PASS / WARN / FAIL aggregation | §10.13 |
| [`report.py`](src/reviewgate/core/report.py) | Suggested-label assembly from warnings + config | §13.9, §12 |
| [`cli.py`](src/reviewgate/core/cli.py) | `reviewgate-core` console script for fixture-driven runs | §5.1, §25 M1 |

### Heuristics in one table

| Warning code | Severity | Trigger | Section |
| ------------ | -------- | ------- | ------- |
| `too_many_files_changed` | medium / high | `files_changed > thresholds.warn / fail.files_changed` | §10.3 |
| `too_large_human_loc` | medium / high | `human_loc_changed > thresholds.warn / fail.human_loc_changed` | §10.3 / §10.4 |
| `weak_pr_body` | medium | empty / whitespace / template-only / < 80 meaningful chars | §10.10 |
| `missing_linked_issue` | medium | no `#123`, `GH-123`, `fixes #…`, external tracker URL, or `ABC-123` | §10.10 |
| `risky_paths_without_rationale` | high | risky paths touched and PR body has no justification | §10.10 |
| `mixed_concerns` | medium | suspicious category cluster (billing + auth + infra, etc.) | §10.11 |
| `config_invalid` | low | `.reviewgate.yml` failed to parse; engine ran with defaults | §12 |

Verdict aggregation (§10.13):

```python
def baseline_reviewability(warnings):
    high = sum(1 for w in warnings if w.severity == "high")
    medium = sum(1 for w in warnings if w.severity == "medium")
    if high >= 2: return "FAIL"
    if high == 1 and medium >= 1: return "FAIL"
    if high == 1 or medium >= 2: return "WARN"
    return "PASS"
```

---

## GitHub Action

The Action wrapper in [`src/reviewgate_action/`](src/reviewgate_action/)
implements [`docs/DESIGN.md` §14](docs/DESIGN.md). The §14 reference
workflow:

```yaml
name: ReviewGate

on:
  pull_request:
    types: [opened, synchronize, edited, reopened]

jobs:
  reviewgate:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      - uses: leo-aa88/reviewgate/src/reviewgate_action@main
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          fail-on: FAIL
          post-comment: true
          mode: action
```

| Input | Default | Purpose |
| ----- | ------- | ------- |
| `github-token` | — | Token to fetch PR data and (when allowed) post the §13 comment. `pull-requests: read` minimum, `write` for posting. |
| `fail-on` | `FAIL` | Verdict at or above which the workflow exits non-zero. One of `PASS`, `WARN`, `FAIL`, `never`. |
| `post-comment` | `"true"` | Whether to upsert the §13 marker comment when §14.1 coexistence allows. |
| `mode` | `auto` | §14.1 coexistence with the hosted App. `auto` defers to `.reviewgate.yml`; `action` forces the Action to post; `quiet` mutes it (logs only). |
| `python-version` | `3.12` | Pin handed to `actions/setup-python`. |
| `working-directory` | `$GITHUB_WORKSPACE` | Where to look up `.reviewgate.yml`. Override only for non-standard checkouts. |

| Output | Description |
| ------ | ----------- |
| `reviewability` | The §10.13 verdict (`PASS` / `WARN` / `FAIL`); empty when the run failed before producing a report. |
| `report-json` | The full §10.2 report as a single-line JSON document; **always valid JSON** (`{}` on failure). |

See [`src/reviewgate_action/README.md`](src/reviewgate_action/README.md) for
the full input/output reference and the §14.1 coexistence rules.

---

## CLI usage

The package installs a `reviewgate-core` console script (§5.1, §25 M1):

```bash
pip install reviewgate
reviewgate-core --input pr.json
cat pr.json | reviewgate-core
```

The CLI reads a §10.1 `EngineInput` JSON document from `--input` (or
stdin) and prints the §10.2 `ReviewabilityReport` to stdout. It is the
canonical local-fixture path; the GitHub Action and the hosted App
both call the same `analyze()` function.

Fourteen golden fixtures live under
[`tests/fixtures/m2_golden/`](tests/fixtures/m2_golden/) covering every
PR shape from §24.2:

```bash
reviewgate-core --input tests/fixtures/m2_golden/06_risky_migration_pr.json | jq .reviewability
# "FAIL"
```

---

## Onboarding

Already running ReviewGate or want the operator-facing setup walkthrough
(hosted App install, repo selection, `.reviewgate.yml`, §14.1
coexistence, required-status-check setup, LLM opt-in policy)? Read
[`docs/ONBOARDING.md`](docs/ONBOARDING.md). It is the discoverability
landing page for new beta teams.

For a hands-on five-minute tutorial that takes you from "clone the
repo" to "merging on a green ReviewGate verdict", read
[`docs/QUICKSTART.md`](docs/QUICKSTART.md).

---

## Docker image

The [`Dockerfile`](Dockerfile) packages the **hosted app** path: `reviewgate-api`
(FastAPI + uvicorn) and `reviewgate-worker` (Dramatiq), with optional extras from
`pyproject.toml`’s `[app]` group (PostgreSQL, Redis, Alembic, etc.). The
deterministic engine alone does not require this image; for local fixture runs
use `pip install reviewgate` / `reviewgate-core` as described in [CLI usage](#cli-usage).

**Build** (from the repository root):

```bash
docker build -t reviewgate:local .
```

Or: `make docker-build` (image name defaults to `reviewgate:local`; override with
`make docker-build IMAGE=myregistry/reviewgate:dev`).

**Run the HTTP API** on port 8000 (bind address is `0.0.0.0` inside the
container). Configure the process via `REVIEWGATE_*` environment variables
(see [`src/reviewgate/app/settings.py`](src/reviewgate/app/settings.py)).
Typical values for a real deployment include:

* `REVIEWGATE_DATABASE_URL` — PostgreSQL DSN for SQLAlchemy (same variable
  Alembic uses for migrations).
* `REVIEWGATE_REDIS_URL` — Redis for Dramatiq and related features.
* `REVIEWGATE_HTTP_PORT` — listening port (default `8000`; the image `EXPOSE`s
  8000).

Example (placeholders only):

```bash
docker run --rm -p 8000:8000 \
  -e REVIEWGATE_DATABASE_URL='postgresql+psycopg://user:pass@host:5432/db' \
  -e REVIEWGATE_REDIS_URL='redis://host:6379/0' \
  reviewgate:local
```

`make docker-run-api` runs the same shape but passes through `REVIEWGATE_DATABASE_URL`
and `REVIEWGATE_REDIS_URL` from your shell if they are already exported.

**Run the worker** by overriding the container command (the default `CMD` is
`reviewgate-api`):

```bash
docker run --rm \
  -e REVIEWGATE_REDIS_URL='redis://host:6379/0' \
  -e REVIEWGATE_DATABASE_URL='postgresql+psycopg://…' \
  reviewgate:local reviewgate-worker
```

Or: `make docker-run-worker` (expects those variables in your environment).

**Migrations** are not run automatically at container start. Apply them with
Alembic using the same `REVIEWGATE_DATABASE_URL`, for example from a one-off
container:

```bash
docker run --rm \
  -e REVIEWGATE_DATABASE_URL='postgresql+psycopg://…' \
  reviewgate:local python -m alembic upgrade head
```

The build context is trimmed by [`.dockerignore`](.dockerignore) (tests, docs,
virtualenvs, and caches are omitted). The image runs as a non-root `reviewgate`
user (UID 1000).

For a **copy-paste local stack** (venv, Postgres + Redis, env vars,
`alembic upgrade head`, `reviewgate-api` + `reviewgate-worker`, and a
`curl /health` check), use **[`docs/HOSTED_LOCAL.md`](docs/HOSTED_LOCAL.md)**.

---

## Makefile (local development)

The [`Makefile`](Makefile) documents itself: run **`make help`** (or plain
`make`) to list targets and short descriptions.

Common workflows:

| Target | Purpose |
| ------ | ------- |
| `make install-dev` | Editable install with `[dev,app]` extras (matches CI). |
| `make test` | Full `pytest` suite. |
| `make check` | Tests plus Ruff lint (`make install-dev` installs Ruff). |
| `make format` | Ruff format on `src/` and `tests/`. |
| `make docker-build` | Build the Docker image (`IMAGE=…` to tag). |
| `make alembic-upgrade` | `alembic upgrade head` (requires `REVIEWGATE_DATABASE_URL`). |

`make lock-uv` / `make sync-uv` are optional helpers for [uv](https://docs.astral.sh/uv/).
`uv.lock` is listed in [`.gitignore`](.gitignore) so local lockfiles do not
pollute the repository; generate one locally if you use uv.

---

## Project layout

```text
reviewgate/
├── src/
│   ├── reviewgate/             # PyPI `reviewgate` top-level package
│   │   ├── __init__.py
│   │   └── core/               # `reviewgate.core` deterministic engine (§4.1)
│   │   ├── engine.py           # public analyze() entry point
│   │   ├── schemas.py          # §10.1 / §10.2 Pydantic models
│   │   ├── config.py           # §12 .reviewgate.yml schema
│   │   ├── categorizer.py      # §10.5 file categorization
│   │   ├── size.py             # §10.3 / §10.4 LOC stats
│   │   ├── pr_body.py          # §10.10 weak-body
│   │   ├── linked_issue.py     # §10.10 linked-issue
│   │   ├── risky_paths.py      # §10.10 risky-paths
│   │   ├── mixed_concern.py    # §10.11 mixed-concern
│   │   ├── aggregate.py        # §10.13 PASS/WARN/FAIL
│   │   ├── report.py           # §13.9 label assembly
│   │   ├── paths.py            # gitignore-style matcher
│   │   └── cli.py              # `reviewgate-core` console script
│   └── reviewgate_action/      # GitHub Action wrapper (§14)
│       ├── action.yml          # composite action contract
│       ├── README.md
│       └── reviewgate_action/  # Python package (import ``reviewgate_action``)
│           ├── fetch_pr.py     # PR + paginated files fetch
│           ├── run_core.py     # config + engine + fail-on + comment
│           ├── coexistence.py  # §14.1 mode resolver
│           └── post_comment.py # §13 marker-comment upsert
├── tests/                       # pytest suite; see CI matrix
│   ├── fixtures/m2_golden/     # 14 §24.2 golden PR fixtures
│   ├── test_core_purity.py     # §4.1 boundary enforcement (AST scan)
│   └── …
├── docs/
│   ├── DESIGN.md               # full product design
│   ├── ONBOARDING.md           # private-beta operator walkthrough
│   └── QUICKSTART.md           # 5-minute tutorial
├── .github/
│   ├── workflows/              # CI + dogfooding LLM PR review
│   ├── ISSUE_TEMPLATE/
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── dependabot.yml
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md
├── CONTRIBUTING.md
├── Dockerfile                  # hosted app image (API + worker)
├── Makefile                    # local dev, tests, Docker, optional uv
├── .dockerignore
├── LICENSE                      # Apache 2.0
├── NOTICE
├── README.md
├── SECURITY.md
├── SUPPORT.md
└── pyproject.toml
```

---

## Status

* **`reviewgate-core` (deterministic engine):** runtime complete.
  All §10 heuristics from `docs/DESIGN.md` are implemented and
  covered by the pytest suite on Python 3.12 and 3.13 (see CI matrix).
* **`reviewgate-action` (GitHub Action):** runtime complete (issues
  #24, #25, #26 landed). Fetches PR metadata, loads `.reviewgate.yml`,
  runs the engine, applies the §14 `fail-on` policy, and (when §14.1
  coexistence allows) upserts the §13 PR comment.
* **Hosted ReviewGate App:** shipped in this tree under
  [`src/reviewgate/app/`](src/reviewgate/app/) and installed with the
  optional `app` extra (`pip install "reviewgate[app]"`). It includes
  the FastAPI surface, webhook receiver, worker, persistence, GitHub
  outputs, and hosted-only LLM report path. Paid ReviewGate Cloud (if
  offered) is a **hosting and operations** layer on top of this code,
  not a closed-source fork. See [`docs/DESIGN.md` §19](docs/DESIGN.md).
* **Public release:** this repository is being prepared for public
  open-source release under Apache 2.0. Until the first signed
  release tag, treat the public API as stable but additive.

---

## Roadmap

The MVP implementation is in beta hardening: core, Action, hosted App,
and hosted LLM paths are in-tree, while public release still depends on
operational beta evidence and a signed release tag. Near-term priorities:

* First public release (`v0.1.0`) on PyPI and GitHub Releases, then
  update Action snippets from `@main` to the signed release tag.
* `pre-commit` hook configuration so the engine can run locally on
  staged changes.
* Optional Action input for status-check name customisation (so a
  team can publish multiple ReviewGate checks side by side).
* Additional heuristics driven by beta feedback (test-coverage delta,
  formatting-only churn detection, dependency-update + behavior-change
  separation per §10.11 examples).

The remaining non-MVP item is the **public PR URL analyzer** (see
[`docs/DESIGN.md` §4.4](docs/DESIGN.md) and
[§28 Future Public PR Analyzer](docs/DESIGN.md)). Hosted App and hosted
LLM implementation live in this repository.

---

## Contributing

Contributions are welcome. Before opening a PR:

1. Project norms and maintainer expectations are summarized in
   [`GOVERNANCE.md`](GOVERNANCE.md). Dependency updates are automated via
   Dependabot — see [`.github/dependabot.yml`](.github/dependabot.yml).
2. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) — the §4.1 purity
   boundary is enforced in CI; any new dependency on a forbidden
   module (network, DB, LLM, GitHub SDK, `subprocess`, …) will fail
   the build.
3. Anchor your change to a §-numbered section of
   [`docs/DESIGN.md`](docs/DESIGN.md) or to an existing issue.
4. Add at least one fixture under
   [`tests/fixtures/m2_golden/`](tests/fixtures/m2_golden/) when you
   add a new heuristic, or a unit test next to an existing one.
5. Run the full suite locally (`pytest`) on Python 3.12+.
6. Follow [Conventional Commits](https://www.conventionalcommits.org)
   for the commit message; PR titles are merged verbatim.

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
By participating you agree to abide by its terms.

### Testing

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,app]"
pytest
```

Equivalent shortcuts: `make venv` then activate, `make install-dev`, and
`make test`. See [Makefile (local development)](#makefile-local-development).

CI runs ``pytest`` on Python **3.12** and **3.13**, Ruff lint checks,
Alembic upgrade/downgrade smoke tests, and package build verification (see
the matrix in [`.github/workflows/ci.yml`](.github/workflows/ci.yml)). This repo
also includes
[`pr-llm-review.yml`](https://github.com/leo-aa88/reviewgate/blob/main/.github/workflows/pr-llm-review.yml),
which can post an LLM-backed PR review when secrets are configured (see the workflow file; fork PRs are skipped).

---

## Security

To report a security vulnerability, please follow [`SECURITY.md`](SECURITY.md).
**Do not open a public issue** for vulnerabilities — use GitHub's
private vulnerability reporting flow or email the maintainer.

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
See [`NOTICE`](NOTICE) for required attributions and third-party
dependency licenses. The full `docs/DESIGN.md` and supporting
documentation are also covered by this license.

```text
Copyright 2025 Leonardo Araujo and ReviewGate contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
```

---

## Acknowledgements

ReviewGate stands on the shoulders of:

* [`pydantic`](https://github.com/pydantic/pydantic) — strict schema
  validation for the §10 engine contract.
* [`PyYAML`](https://pyyaml.org) — `.reviewgate.yml` parsing.
* [`pathspec`](https://github.com/cpburnz/python-pathspec) — pure
  gitignore-style glob matching for the §10.6–§10.9 path patterns.
* [Conventional Commits](https://www.conventionalcommits.org) and
  [Keep a Changelog](https://keepachangelog.com) for project hygiene.

The product thesis owes a lot to every senior engineer who has ever
clicked **Approve** on a PR they were too tired to actually review.
