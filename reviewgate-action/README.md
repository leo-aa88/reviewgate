# reviewgate-action

GitHub Action wrapper around [`reviewgate-core`](../) that runs the deterministic reviewability engine on a pull request and (optionally, once #26 lands) posts the §13 summary comment. See `docs/DESIGN.md` §14 for the full design.

> **Status: runtime preview (issues #24 + #25 landed; #26 pending).** The Action fetches PR metadata, loads `.reviewgate.yml`, runs the deterministic engine, prints the §10.2 report, and applies the §14 `fail-on` policy. The optional PR-comment upsert and `mode` coexistence skip behaviour land in #26; the `post-comment` and `mode` inputs are accepted and validated today but are otherwise no-ops. Pinning the Action as a required status check is now safe: the engine result drives the workflow exit code via `fail-on`.

## Implementation status

| Step | Module | Issue | Lands |
| ---- | ------ | ----- | ----- |
| Fetch PR metadata + paginated files | [`reviewgate_action.fetch_pr`](src/reviewgate_action/fetch_pr.py) | #24 | done |
| Load `.reviewgate.yml`, run core, apply `fail-on` | [`reviewgate_action.run_core`](src/reviewgate_action/run_core.py) | #25 | done |
| Mode coexistence + PR comment upsert | (TBD) | #26 | pending |

Local invocation outside Actions:

```bash
GITHUB_TOKEN=ghp_xxx \
GITHUB_REPOSITORY=owner/repo \
GITHUB_EVENT_PATH=/path/to/pull_request_event.json \
python -m reviewgate_action.fetch_pr --output engine.json
python -m reviewgate_action.run_core --input engine.json --workspace . \
    --fail-on FAIL --output-json report.json
```

## Usage

The §14 reference workflow:

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
      pull-requests: read
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
| `github-token` | yes | — | Token used to fetch PR metadata and (when `post-comment: true`, in #26) post the summary comment. Needs `pull-requests: read` and `contents: read` (`reviewgate_action.fetch_pr` calls `GET /repos/{owner}/{repo}/pulls/{n}` and the paginated `/files` endpoint); `pull-requests: write` will additionally be required for the comment-upsert path landing in #26. |
| `fail-on` | no | `FAIL` | Verdict at or above which the workflow exits non-zero. One of `PASS`, `WARN`, `FAIL`, `never`. `never` always exits 0. |
| `post-comment` | no | `"true"` | Whether to upsert the §13 ReviewGate marker comment on the PR. Accepted today but no-op until #26 lands the comment-upsert step. |
| `mode` | no | `auto` | Coexistence with the hosted ReviewGate App (§14.1). One of `auto`, `action`, `quiet`. `auto` defers to `.reviewgate.yml`. The skip behaviour itself lands in #26. |
| `python-version` | no | `3.12` | Python version pin handed to `actions/setup-python`. The §15 stack requires 3.12+; override only to bump the patch release. |
| `working-directory` | no | `""` | Workspace root used to look up `.reviewgate.yml`. Defaults to `$GITHUB_WORKSPACE`. Override only for non-standard checkouts (e.g. monorepo subdirectory pinned via `actions/checkout`'s `path:` input). |

## Outputs

| Name | Description |
| ---- | ----------- |
| `reviewability` | The §10.13 baseline verdict (`PASS` / `WARN` / `FAIL`). Empty when the run failed before the engine produced a report (invalid input, missing token, etc.). |
| `report-json` | The full §10.2 report as a single-line JSON string. Empty when the run failed before the engine produced a report. Consumers parse with `${{ fromJSON(steps.x.outputs.report-json) }}` to drive downstream steps. |

## Coexistence with the hosted App (§14.1)

`.reviewgate.yml` carries a `mode` field that controls which surface posts comments and status checks:

```yaml
mode: app # app | action | both
```

| `.reviewgate.yml` `mode` | Action default behaviour (after #26) |
| ------------------------ | ------------------------------------ |
| `app` | Action stays quiet (skips comment and status check). |
| `action` | Action posts comment and status check; hosted App skips. |
| `both` | Both run; the Action namespaces its status check name to avoid collision. |

The Action's `mode` input overrides the YAML when set explicitly to `action` or `quiet`. `mode: auto` (the default) is the only value that defers to `.reviewgate.yml`. The skip-on-`mode: app` behaviour itself lands in #26; until then the Action runs the engine regardless of `mode`.
