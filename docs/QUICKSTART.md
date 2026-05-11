# ReviewGate Quickstart

This is the five-minute tutorial. By the end you will:

1. Install `reviewgate-core` locally and run the deterministic engine
   on a fixture pull request.
2. Wire the open-source `reviewgate-action` into your repository's CI
   so it analyses every PR and owns Action-side enforcement.
3. Tune `.reviewgate.yml` for your project and (optionally) make
   ReviewGate a required status check.

If you only want the *operator* walkthrough for the hosted ReviewGate
App + private beta install, read [`docs/ONBOARDING.md`](ONBOARDING.md)
instead. For the full design, see [`docs/DESIGN.md`](DESIGN.md).

---

## 0. Prerequisites

* Python **3.12+** (see `docs/DESIGN.md` §15).
* `git` and a GitHub repository where you can push a workflow file.
* (Optional) `uv` or `pipx` if you'd rather not manage virtualenvs by
  hand.

---

## 1. Install `reviewgate-core` and run the CLI on a fixture

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"     # core + Action development from a clone
# or, once published:
# pip install reviewgate
```

The package ships a small CLI named `reviewgate-core` (see
`src/reviewgate/core/cli.py`). Feed it a JSON document matching the
§10.1 `EngineInput` schema and it prints the §10.2
`ReviewabilityReport` to stdout. The repo already contains 14 golden
fixtures under [`tests/fixtures/m2_golden/`](../tests/fixtures/m2_golden);
run any of them:

```bash
reviewgate-core --input tests/fixtures/m2_golden/06_risky_migration_pr.json | jq .reviewability
# "FAIL"
```

Or pipe a payload from stdin:

```bash
cat tests/fixtures/m2_golden/01_small_clean_pr.json | reviewgate-core
```

A minimal `EngineInput` from scratch looks like this:

```json
{
  "pr": {
    "title": "Add /healthz endpoint",
    "body": "Closes #42. Adds a liveness probe used by k8s.",
    "author": "alice",
    "base_branch": "main",
    "head_branch": "alice/healthz",
    "additions": 18,
    "deletions": 0,
    "changed_files": 2
  },
  "files": [
    {
      "filename": "app/healthz.py",
      "status": "added",
      "additions": 14,
      "deletions": 0,
      "changes": 14
    },
    {
      "filename": "tests/test_healthz.py",
      "status": "added",
      "additions": 4,
      "deletions": 0,
      "changes": 4
    }
  ],
  "config": {}
}
```

Saving that to `pr.json` and running `reviewgate-core --input pr.json`
will produce a `PASS` verdict, a clean warnings list, and the §10.5
file categories (`source`, `test`).

> **Note: the engine is pure** (`docs/DESIGN.md` §4.1). It does not
> call GitHub, the network, the filesystem, or an LLM — the input JSON
> is the only thing it sees. The `reviewgate-action` runtime
> (`src/reviewgate_action/`) is the thin layer that fetches the PR from
> GitHub and feeds the engine.

---

## 2. Wire the GitHub Action into your repository

Add this workflow file at `.github/workflows/reviewgate.yml`:

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
      pull-requests: write   # `read` is enough if you set post-comment: false
    steps:
      - uses: actions/checkout@v4
      # Pre-release docs use @main until the first public tag is cut.
      # After v0.1.0, pin a release tag instead.
      - uses: leo-aa88/reviewgate@main
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          fail-on: FAIL          # never | PASS | WARN | FAIL
          post-comment: true     # honoured only when §14.1 coexistence allows
          mode: action           # action owns comments + fail-on for this tutorial
```

Open a PR. Within a minute you should see:

* A workflow run titled `ReviewGate` in the **Checks** tab.
* The job log printing the §10.2 report and a Markdown summary on the
  workflow summary page (`$GITHUB_STEP_SUMMARY`).
* (When `mode` allows) a single PR comment from `github-actions[bot]`
  carrying the hidden `<!-- reviewgate-report -->` marker. On the next
  push the same comment is **updated in place**, not duplicated.

**The Action's two outputs** are useful when you want to chain the
verdict into downstream steps:

```yaml
- id: rg
  uses: leo-aa88/reviewgate@main
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}
    fail-on: never               # let the next step decide
    post-comment: false

- if: steps.rg.outputs.reviewability == 'FAIL'
  run: echo "::warning::ReviewGate marked this PR unreviewable."

- run: |
    echo "Files changed: ${{ fromJSON(steps.rg.outputs.report-json).stats.files_changed }}"
```

`report-json` is always valid JSON: when the run fails before the
engine produces a report (e.g. fetch error) it falls back to `{}` so
`fromJSON()` never crashes the consumer expression.

---

## 3. Tune `.reviewgate.yml`

Drop a file named `.reviewgate.yml` at the repository root on the
default branch. The §12 schema is fully documented; this is the
recommended starter:

```yaml
version: 1

# §14.1 coexistence with the hosted ReviewGate App. Use `action` for
# this Action-only quickstart so `mode: auto` workflows do not stay
# quiet. Hosted-App beta repos normally use `app`; `both` lets both
# surfaces post their own marker comment.
mode: action

# §10.3 thresholds — lower for stricter gating, raise for monorepos
# with intentionally large PRs. The defaults match docs/DESIGN.md
# verbatim, so omitting any block inherits the documented value.
thresholds:
  warn:
    files_changed: 25
    human_loc_changed: 800
  fail:
    files_changed: 75
    human_loc_changed: 2500

# §10.6 — paths the engine treats as risky. Defaults already cover
# migrations, auth, billing, payments, infra/terraform, and
# .github/workflows; add your own globs.
risky_paths:
  - "**/migrations/**"
  - "infra/**"
  - "src/payments/**"

# §10.10 policy switches. `require_linked_issue` makes the engine emit
# `missing_linked_issue` when the title/body has no #123, GH-123,
# fixes #..., or external tracker URL.
policy:
  require_linked_issue: true
  require_human_summary: true
  fail_on_risky_paths_without_context: true

# §13.9 — labels applied by the hosted App. The Action does not
# manage labels yet (#52); listing them here is harmless.
labels:
  pass: reviewability-pass
  warn: reviewability-warn
  fail: reviewability-fail

# §13.10 — name of the GitHub status check; pin only if your branch
# protection encodes a different name.
status_check:
  name: reviewgate/reviewability
  fail_on: FAIL
  warn_blocks_merge: false

# §21.3 — opt-in only. The hosted App reads this; the open-source
# Action ignores it (no LLM in `reviewgate-core` or `reviewgate-action`
# per §11). Stays false until you confirm during beta onboarding.
llm_reports: false
```

A few important properties:

* **Malformed config does not break the run** (§12). If you typo a
  key, the engine emits a `config_invalid` warning and runs against
  defaults. You won't get a green CI on a broken file by accident — the
  warning is visible in the PR comment.
* **Schema is strict**: unknown top-level keys fail validation, and
  the warning message names the offending key.
* **Defaults match `docs/DESIGN.md`** exactly. You can leave any
  block unset and inherit the documented default value.

---

## 4. Make ReviewGate a required status check (optional)

The Action runs as a regular GitHub Actions job, so its check name is
the workflow `jobs.<id>.name` — by default `ReviewGate / reviewgate`.
To gate merges on it:

1. **Settings → Branches → Add rule** (or edit the existing rule for
   `main`).
2. Tick **Require status checks to pass before merging**.
3. In the search box, type `reviewgate` and select the check.
4. Save.

Branch protection now blocks merges whenever the engine returns a
`FAIL` verdict (or `WARN`, if you set `fail-on: WARN`). This is the
moment ReviewGate stops being advice and becomes a workflow gate.

---

## 5. Triage a WARN or FAIL

When ReviewGate flags a PR, the comment lists every deterministic
warning by code. Common ones:

| Code | What to do |
| ---- | ---------- |
| `weak_pr_body` | Add at least a sentence (the §10.10 threshold is 80 meaningful characters after stripping template scaffolding) describing *why* the change exists. |
| `missing_linked_issue` | Link the tracker (`Closes #123`, `GH-123`, `ABC-123`, or a Jira / Linear / GitHub issue URL). |
| `risky_paths_without_rationale` | Add a paragraph in the body referencing the risky category (e.g. `migrations`, `auth`, `payments`) or the file path so the bot finds the justification. |
| `too_many_files_changed` / `too_large_human_loc` | Split the PR. Generated, lockfile, snapshot, minified, and vendored files **do not count** toward `human_loc_changed`, so a large dependency bump usually does not trigger this. |
| `mixed_concerns` | The category cluster looks unrelated (§10.11). Split or update the description to explain why these areas are touched together. |

Push another commit (or edit the PR body). The Action reruns
automatically and updates the existing comment in place — the marker
keeps the upsert stable.

> If you later install the hosted App on the same repository, change
> `.reviewgate.yml` back to `mode: app` (or omit the key) and leave the
> workflow input at `mode: auto` so the Action becomes log-only while
> the App owns comments, labels, and the status check.

---

## 6. Hosted app (dev): Dramatiq worker (Redis)

The hosted GitHub App runs long PR analysis jobs out-of-process using
**Dramatiq** with a **Redis** broker (``docs/DESIGN.md`` §13.7). Issue #30 wires
the worker entrypoint; issue #32 adds the FastAPI API process (``reviewgate-api``).

From a repository clone:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[app]"
export REVIEWGATE_REDIS_URL=redis://127.0.0.1:6379/0
```

Start Redis locally (Docker example: ``docker run -p 6379:6379 redis:7-alpine``),
then in a **second terminal** with the same venv and ``REVIEWGATE_REDIS_URL``:

```bash
reviewgate-worker
# equivalent explicit invocation:
python -m dramatiq reviewgate.app.analysis.worker_app
```

The worker loads ``reviewgate.app.analysis.worker_app``, installs the
broker from ``AppSettings``, and imports the hosted analysis actors in
``reviewgate.app.analysis.jobs``.

In a **third terminal** (same venv; no extra env vars required for the default
``GET /health`` probe):

```bash
reviewgate-api
# then:
curl -s http://127.0.0.1:8000/health
```

---

## 7. Where to go next

* [`docs/DESIGN.md`](DESIGN.md) — full product design, every
  threshold, every warning code, every architectural decision.
* [`docs/ONBOARDING.md`](ONBOARDING.md) — walkthrough for hosted-App
  beta teams.
* [`src/reviewgate_action/README.md`](../src/reviewgate_action/README.md) —
  full input/output reference and the §14.1 coexistence rules.
* [`CONTRIBUTING.md`](../CONTRIBUTING.md) — read this before opening a
  PR. The §4.1 purity boundary is enforced in CI.
