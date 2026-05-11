# ReviewGate -- Private Beta Onboarding

This guide walks a new ReviewGate beta team through installation, repo
selection, optional configuration, the GitHub Action wrapper, and
turning ReviewGate into a required status check. Read it once; the
runtime ships with sensible defaults so a "do nothing" install still
posts useful comments.

> **What ReviewGate does in one sentence.** It checks whether a pull
> request is *reviewable* (size, scope, context, risky paths,
> linked issues) before humans spend time on it; the open-source
> deterministic engine produces the verdict, and the hosted App or
> the GitHub Action wraps it for posting on the PR. See `docs/DESIGN.md`
> §1-§3 for the full pitch.

> **Heads-up: LLM reports are opt-in.** ReviewGate ships with
> `llm_reports: false` per `docs/DESIGN.md` §21.3 and §12. No PR
> content is sent to an LLM provider until you (a) flip
> `llm_reports: true` in `.reviewgate.yml` and (b) confirm during
> beta onboarding. Deterministic-only mode is always available and
> never sends PR content off your repository.
>
> **Hosted deployments:** enabling `llm_reports: true` also requires the
> operator to set **`REVIEWGATE_OPENAI_API_KEY`** on the App service (plus
> optional **`REVIEWGATE_LLM_MODEL`** and **`REVIEWGATE_OPENAI_API_BASE_URL`**
> for OpenAI-compatible gateways). Without a key, the worker keeps the
> deterministic report and skips provider calls.

---

## 1. Pick your install path

ReviewGate has two surfaces. Most teams use both; you can also
install only one.

| Surface | Who hosts | Posting authority | Required for |
| ------- | --------- | ----------------- | ------------ |
| **Hosted GitHub App** | ReviewGate | The §13 PR comment + the §13 status check | Friction-free install, no workflow file needed |
| **Open-source GitHub Action** | You (runs in your Actions runner) | Optional: the Action can stay quiet, or own the comment, or coexist with the App | Self-hosted teams, fully open-source path, custom workflow integration |

The two surfaces are designed to coexist: `.reviewgate.yml`'s `mode`
field decides which one posts so you never get duplicate comments.
See [§5. Coexistence](#5-action-and-app-coexistence-mode) below.

---

## 2. Install the hosted GitHub App

`docs/DESIGN.md` §8.1 documents the install flow; this section is the
operator-facing walkthrough.

1. Visit the ReviewGate landing page and click **Install ReviewGate**
   (or **Join Beta** if invitation-only).
2. Authorize the App for the GitHub organization (or personal
   account) that owns the repos you want to gate.
3. On the install screen, choose **Only select repositories** and
   pick the repos you want ReviewGate to analyze. You can extend the
   list later from the App's settings page; we recommend starting
   with one or two repos to validate behaviour before rolling
   organisation-wide.
4. The App needs the following permissions; GitHub asks once at
   install time:
   * **Pull requests** -- read & write (read PR metadata + files;
     write the §13 comment and labels)
   * **Contents** -- read (read `.reviewgate.yml` from the base
     branch)
   * **Checks** -- write (publish the §13 status check)
   * **Metadata** -- read (mandatory; granted automatically by
     GitHub)
5. The App stores the installation id and your selected repos and
   begins analysing future PRs (§8.2). Existing open PRs are *not*
   automatically rescanned -- push a new commit, edit the PR body,
   or reopen the PR to trigger an analysis.

**Verifying the install.** Open one of the selected repos, create a
throwaway PR with `Hello reviewgate` as the body, and within a
minute you should see:

* a comment posted by `reviewgate-app[bot]` with the §13 marker, and
* a `reviewgate/reviewability` status check with verdict
  `PASS` / `WARN` / `FAIL`.

If the comment never arrives, check **Repository settings ->
Integrations & services -> ReviewGate -> Recent deliveries** for
webhook errors.

---

## 3. Add `.reviewgate.yml` (optional but recommended)

`.reviewgate.yml` lives at the repository root on the **base branch**
(typically `main`). The §12 schema is fully documented; this section
covers the most common starter config.

```yaml
# .reviewgate.yml
version: 1

# §14.1 coexistence -- which surface posts the §13 comment.
# Defaults to `app` so the hosted App owns posting and the GitHub
# Action (if installed) stays quiet on the PR surface.
mode: app

# Default thresholds (§10.3). Lower these for stricter gating;
# raise them for monorepos with intentionally large PRs.
thresholds:
  warn:
    files_changed: 25
    human_loc_changed: 800
  fail:
    files_changed: 75
    human_loc_changed: 2500

# §10.6 risky-path overrides. The defaults already cover migrations,
# auth, billing, payments, infra/terraform, and `.github/workflows`;
# add your own globs here. Each path matched as risky requires a
# rationale in the PR body to avoid `risky_paths_without_rationale`.
risky_paths:
  - "**/migrations/**"
  - "infra/**"
  - "src/payments/**"

# §21.3 -- LLM reports are off by default and stay off unless you
# flip this AND confirm during beta onboarding. Deterministic-only
# mode never sends PR content off your repository.
llm_reports: false

# §13.10 -- name of the GitHub status check the App publishes.
# Override only if a different check naming convention is required
# for branch protection in your org.
status_check:
  name: "reviewgate/reviewability"
  fail_on: WARN
```

A few things worth knowing:

* **Malformed config does not break the run.** Per §12, ReviewGate
  attaches a `config_invalid` warning to the report and runs against
  defaults so a typo never blocks a PR.
* **Schema validation is strict.** Unknown top-level keys fail
  validation and trigger the malformed-config recovery path; you
  see the exact key name in the warning, so a `Did you mean
  `thresholds`?` debugging loop is short.
* **Defaults match `docs/DESIGN.md`.** Every threshold the §10.3
  table lists has the same value here; you can leave any block
  unset and inherit the documented default.

---

## 4. Add the GitHub Action (optional)

If you want a fully open-source path, run ReviewGate from your own
GitHub Actions runner instead of (or alongside) the hosted App. The
Action lives at [`src/reviewgate_action/`](../src/reviewgate_action/) in this
repo; the §14 reference workflow:

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
      pull-requests: read       # add `write` to let the Action post the §13 comment
    steps:
      - uses: actions/checkout@v4
      # Pre-release docs use @main until the first public tag is cut.
      # After v0.1.0, pin a release tag instead.
      - uses: leo-aa88/reviewgate/src/reviewgate_action@main
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          fail-on: FAIL          # never | PASS | WARN | FAIL (default: FAIL)
          post-comment: true     # honoured only when §14.1 coexistence allows
          mode: auto             # auto | action | quiet (default: auto -> defer to .reviewgate.yml)
```

What the Action does, in order:

1. Pins Python via `actions/setup-python@v5` (`python-version` input,
   default 3.12).
2. Installs `reviewgate-core` + `reviewgate-action` into the runner.
3. Calls `reviewgate_action.fetch_pr` to read the PR + paginated
   files list from the GitHub REST API.
4. Calls `reviewgate_action.run_core` to load `.reviewgate.yml`,
   run the deterministic engine, write the §10.2 report to
   `${{ steps.<id>.outputs.report-json }}` (always valid JSON; falls
   back to `{}` on failure paths so `fromJSON()` never crashes
   downstream), and write a Markdown summary to
   `$GITHUB_STEP_SUMMARY`.
5. Resolves §14.1 coexistence (see §5 below). When coexistence
   allows, upserts the §13 ReviewGate marker comment on the PR.
6. Applies the `fail-on` policy: exit 0 below the threshold, exit 1
   at or above it. `never` always exits 0 even on a FAIL verdict.

Comment-posting failures (missing token, 403 from a token without
`pull-requests: write`, etc.) emit a `::error::` annotation so the
misconfiguration shows up in the PR check summary, but they do **not**
flip the workflow exit code -- the engine verdict via `fail-on`
always drives the workflow result.

---

## 5. Action and App coexistence (`mode`)

ReviewGate is designed so the App and the Action stay in their
lanes. The §14.1 rules:

| Action `mode` input | `.reviewgate.yml` `mode` | Who posts the §13 comment |
| ------------------- | ------------------------ | ------------------------ |
| `mode: quiet` | (any) | Neither -- Action stays silent, hosted App keeps posting normally. The Action also ignores `fail-on` so the workflow exits 0. Use this during a hosted-App migration or to mute the Action while debugging. |
| `action` | (any) | Action posts. Configure your hosted App to skip if you have both installed (otherwise you'll get duplicate comments). |
| `auto` (default) | `app` (default) | Hosted App. Action stays quiet on the PR surface but still produces the workflow log + summary. |
| `auto` | `action` | Action. Hosted App is expected to skip on its side. |
| `auto` | `both` | Both. The §13 marker keeps each comment distinct so neither edits the other. |

For most teams the right starting point is:

* Install the hosted App.
* Install the Action with `mode: auto`.
* Keep the default `.reviewgate.yml` `mode: app`.

The Action then runs in the workflow log for visibility, while the
App owns the user-facing PR surface. If you later disable the App
(e.g. an outage drill), flip `mode: action` in `.reviewgate.yml` and
the Action takes over without touching the workflow file.

---

## 6. Make ReviewGate a required status check

Branch protection (§8.4) is what turns "ReviewGate posted a
comment" into "FAIL blocks merge". The flow:

1. Open **Repository settings -> Branches**.
2. Click **Add rule** (or edit an existing rule) for the branch you
   want to protect (typically `main`).
3. Enable **Require a pull request before merging** and **Require
   status checks to pass before merging**.
4. In the search box for required checks, type `reviewgate` and
   pick the check you want gated:
   * `reviewgate/reviewability` -- the hosted App's §13 check.
     Pick this if the App is the source of truth.
   * `ReviewGate / reviewgate` -- the GitHub Action's check name
     (the workflow `jobs.<id>.name` value). Pick this if you run
     the Action with `fail-on: FAIL` and want its exit code to gate
     merges.
5. Save.

A few rules of thumb:

* **Pick exactly one** ReviewGate check as required. Listing both
  the App and the Action means a transient outage on either side
  blocks merges; pick the surface you trust to be available.
* **Avoid pinning `mode: both` as a required check on both
  surfaces.** The two checks publish independently; one being slow
  blocks merges even when the other has already passed.
* **`fail-on: WARN` is a deliberate strictness dial.** Default `FAIL`
  is the broadest acceptance; `WARN` blocks merges on every
  borderline-reviewable PR and is appropriate once your team has
  internalised the §10 rubric.

---

## 7. Triage a WARN or FAIL

When ReviewGate flags a PR, the §13 comment lists every
deterministic warning by code (e.g. `too_large_human_loc`,
`weak_pr_body`, `risky_paths_without_rationale`,
`missing_linked_issue`). The §8.3 author-fix flow:

1. Read the report.
2. Address the highest-severity warnings first. Common fixes:
   * **`weak_pr_body`** -- write at least a sentence (§10.10
     threshold: 80 meaningful characters after stripping template
     scaffolding) describing *why* the change exists.
   * **`missing_linked_issue`** -- link the tracker (`Closes #123`,
     `GH-123`, `ABC-123`, or a Jira / Linear / GitHub issue URL).
   * **`risky_paths_without_rationale`** -- add a paragraph in the
     body referencing the risky category (e.g. `migrations`,
     `auth`, `payments`) or the file path so the bot finds the
     justification.
   * **`too_many_files_changed` / `too_large_human_loc`** -- split
     the PR. Generated, lockfile, snapshot, minified, and vendored
     files do not count toward `human_loc_changed`, so a large
     dependency bump usually does not trigger this.
3. Push another commit (or edit the PR body). ReviewGate reruns
   automatically and updates the existing §13 comment in place
   (the marker keeps the upsert stable).

---

## 8. Where to ask for help

* **Open issues** at <https://github.com/leo-aa88/reviewgate>
  for bugs, feature requests, or surprising verdicts. Include the
  PR URL or a redacted §10.2 report so the team can reproduce.
* **Beta Slack / email** is the right channel for "I want LLM
  reports turned on for repo X" -- the founder confirms the LLM
  policy with you in writing before flipping the bit per §21.3.
* **`docs/DESIGN.md`** is the source of truth for every threshold,
  warning code, and behaviour described above. The README's
  "Cross-reference index" links to the relevant §-numbered
  sections.
