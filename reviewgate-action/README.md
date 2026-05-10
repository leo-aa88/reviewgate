# reviewgate-action

GitHub Action wrapper around [`reviewgate-core`](../) that runs the deterministic reviewability engine on a pull request and (optionally, once #26 lands) posts the §13 summary comment. See `docs/DESIGN.md` §14 for the full design.

> **Status: runtime complete (issues #24, #25, #26 landed).** The Action fetches PR metadata, loads `.reviewgate.yml`, runs the deterministic engine, prints the §10.2 report, applies the §14 `fail-on` policy, and (when §14.1 coexistence allows) upserts the §13 PR comment. By default (no `.reviewgate.yml` -> `mode: app`) the Action runs the engine for the workflow log + summary but stays quiet on the PR surface so the hosted App owns posting; switch to `mode: action` (per-workflow input or in `.reviewgate.yml`) to let the Action post.

## Implementation status

| Step | Module | Issue | Lands |
| ---- | ------ | ----- | ----- |
| Fetch PR metadata + paginated files | [`reviewgate_action.fetch_pr`](src/reviewgate_action/fetch_pr.py) | #24 | done |
| Load `.reviewgate.yml`, run core, apply `fail-on` | [`reviewgate_action.run_core`](src/reviewgate_action/run_core.py) | #25 | done |
| §14.1 mode coexistence + §13 PR-comment upsert | [`reviewgate_action.coexistence`](src/reviewgate_action/coexistence.py) + [`reviewgate_action.post_comment`](src/reviewgate_action/post_comment.py) | #26 | done |

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
| `post-comment` | no | `"true"` | Whether to upsert the §13 ReviewGate marker comment on the PR. Honoured only when §14.1 coexistence permits the Action to post (i.e. `mode: action`, or `mode: auto` + `.reviewgate.yml` `mode: action` / `both`). Set `false` to keep the Action quiet on the PR surface even when coexistence would allow posting. **Comment-posting is auxiliary**: a failure (missing token, 403 from a token without `pull-requests: write`, etc.) is logged as `::error::` so it is highly visible in the PR check summary, but the workflow exit code is always driven by the engine verdict via `fail-on` so a posting hiccup cannot silently flip a PASS run to FAIL or vice versa. |
| `mode` | no | `auto` | Coexistence with the hosted ReviewGate App (§14.1). One of `auto`, `action`, `quiet`. `auto` defers to `.reviewgate.yml` (default `mode: app` -> Action stays quiet). `action` makes the Action own the surface regardless of `.reviewgate.yml`. `quiet` mutes the Action entirely (no comment, `fail-on` is ignored, exit 0). |
| `python-version` | no | `3.12` | Python version pin handed to `actions/setup-python`. The §15 stack requires 3.12+; override only to bump the patch release. |
| `working-directory` | no | `""` | Workspace root used to look up `.reviewgate.yml`. Defaults to `$GITHUB_WORKSPACE`. Override only for non-standard checkouts (e.g. monorepo subdirectory pinned via `actions/checkout`'s `path:` input). |

## Outputs

| Name | Description |
| ---- | ----------- |
| `reviewability` | The §10.13 baseline verdict (`PASS` / `WARN` / `FAIL`). Empty when the run failed before the engine produced a report (invalid input, missing token, etc.). Equality checks against the three literal values cleanly miss the empty case. |
| `report-json` | The full §10.2 report as a single-line JSON string. **Always valid JSON.** When the run failed before the engine produced a report this falls back to `{}` so `${{ fromJSON(steps.x.outputs.report-json) }}` never crashes the consumer's workflow expression; downstream steps that need the verdict should branch on `reviewability` first. |

## Coexistence with the hosted App (§14.1)

`.reviewgate.yml` carries a `mode` field that controls which surface posts comments and status checks:

```yaml
mode: app # app | action | both
```

| `.reviewgate.yml` `mode` | Action default behaviour |
| ------------------------ | ------------------------ |
| `app` (default per §12) | Action stays quiet (no comment, `fail-on` ignored). The hosted App owns the surface. |
| `action` | Action posts the §13 comment; hosted App is expected to skip on its side. |
| `both` | Both surfaces post. The §13 marker keeps each comment distinct so neither edits the other. |

The Action's `mode` input overrides the YAML when set explicitly to `action` or `quiet`. `mode: auto` (the default) is the only value that defers to `.reviewgate.yml`.

| Action `mode` | `.reviewgate.yml` `mode` | Decision |
| ------------- | ------------------------ | -------- |
| `quiet` | (any) | No comment, `fail-on` ignored. The Action exits 0 regardless of verdict; the workflow log + `$GITHUB_STEP_SUMMARY` still carry the report. |
| `action` | (any) | Action owns posting. `post-comment` and `fail-on` apply normally. |
| `auto` | `app` | Hosted App posts. The Action stays quiet (no comment, `fail-on` ignored). |
| `auto` | `action` or `both` | Action posts. `post-comment` and `fail-on` apply normally. |
