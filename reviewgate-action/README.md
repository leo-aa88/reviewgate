# reviewgate-action

GitHub Action wrapper around [`reviewgate-core`](../) that runs the deterministic reviewability engine on a pull request and (optionally) posts the §13 summary comment. See `docs/DESIGN.md` §14 for the full design.

> **Status:** scaffold (issue #23). The Action's input contract is final; the runtime (`runs.using: composite` step) is a placeholder that validates inputs and exits cleanly. PR fetch, core invocation, and comment upsert land in issues #24–#26.

## Usage

The §14 reference snippet:

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

When this monorepo is split per `docs/DESIGN.md` §14 ("Repository: `github.com/reviewgate/reviewgate-action`"), consumers will reference `reviewgate/reviewgate-action@v1` instead. The input contract stays identical.

## Inputs

| Name | Required | Default | Description |
| ---- | -------- | ------- | ----------- |
| `github-token` | yes | — | Token used to fetch PR metadata and (when `post-comment: true`) post the summary comment. Needs `pull-requests: read` at minimum; `pull-requests: write` is required for `post-comment: true`. |
| `fail-on` | no | `FAIL` | Verdict at or above which the workflow exits non-zero. One of `PASS`, `WARN`, `FAIL`, `never`. |
| `post-comment` | no | `"true"` | Whether to upsert the §13 ReviewGate marker comment on the PR. |
| `mode` | no | `auto` | Coexistence with the hosted ReviewGate App (§14.1). One of `auto`, `action`, `quiet`. `auto` defers to `.reviewgate.yml`. |

## Outputs (planned -- arrive with the runtime in #25)

The §14 design intends two public outputs once the runtime lands:

| Name | Description |
| ---- | ----------- |
| `reviewability` | The §10.13 baseline verdict (`PASS` / `WARN` / `FAIL`). Empty when the Action did not run (e.g. `mode: quiet`). |
| `report-json` | The full §10.2 report as a JSON string. Empty when the Action did not run. Consumers should parse with `fromJSON()`. |

These are intentionally **not** declared on the scaffold's `action.yml`: composite outputs must reference a real step output, and emitting empty placeholders would silently break `if: steps.x.outputs.reviewability == 'PASS'` checks and crash `fromJSON(steps.x.outputs.report-json)` consumers. Both outputs are wired together with the core runtime in #25 so every consumer always receives a valid value.

## Coexistence with the hosted App (§14.1)

`.reviewgate.yml` carries a `mode` field that controls which surface posts comments and status checks:

```yaml
# .reviewgate.yml
mode: app # app | action | both
```

| `.reviewgate.yml` `mode` | Action default behaviour |
| ------------------------ | ------------------------ |
| `app` | Action stays quiet (skips comment and status check). |
| `action` | Action posts comment and status check; hosted App skips. |
| `both` | Both run; the Action namespaces its status check name to avoid collision. |

The Action's `mode` input overrides the YAML when set explicitly to `action` or `quiet`. `mode: auto` (the default) is the only value that defers to `.reviewgate.yml`.
