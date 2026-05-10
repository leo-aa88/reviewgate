# reviewgate-action

GitHub Action wrapper around [`reviewgate-core`](../) that runs the deterministic reviewability engine on a pull request and (optionally) posts the §13 summary comment. See `docs/DESIGN.md` §14 for the full design.

> **Status: scaffold (issue #23). Do not pin as a required status check yet.** The composite step validates inputs and then **exits non-zero** with `::error::` so a workflow that names this Action as a required check cannot silently report success while the review pipeline is missing. PR fetch (#24), core invocation + `fail-on` (#25), and mode coexistence + comment (#26) are the follow-on issues. The input contract below is final; only the runtime is missing.

## Usage

The §14 reference snippet (intended for use once #24–#26 land; running it against the current scaffold will fail the step on purpose):

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

> **`uses:` path syntax.** GitHub Actions resolves `{owner}/{repo}/{path}@{ref}` as the subdirectory action at `{path}/action.yml` in `{owner}/{repo}`. This Action's `action.yml` lives at [`reviewgate-action/action.yml`](action.yml), so `leo-aa88/reviewgate-core/reviewgate-action@v1` is the documented subdirectory reference. See GitHub's "Using actions" docs ("Referencing an action in the same repository where a workflow file uses the action" and "Referencing an action in a different repository") for the spec.

When this monorepo is split per `docs/DESIGN.md` §14 ("Repository: `github.com/reviewgate/reviewgate-action`"), consumers will reference `reviewgate/reviewgate-action@v1` instead -- the `<path>` segment simply collapses out and the input contract stays identical.

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
