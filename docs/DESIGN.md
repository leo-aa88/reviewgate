# ReviewGate Design Document

## 1. Product Summary

ReviewGate is a developer tool that checks whether a GitHub pull request is **reviewable** before humans spend time reviewing it.

It is not a full code reviewer. It does not claim code correctness, security, production readiness, or merge safety. Its job is narrower:

> Should this pull request enter human review right now?

ReviewGate evaluates pull requests for review burden, scope clarity, risky paths, missing context, and whether the PR should be split before review.

The product has three layers:

1. **reviewgate-core**

   * Open-source deterministic reviewability engine.
   * Computes PR stats, file categories, warnings, labels, and baseline PASS/WARN/FAIL.

2. **reviewgate-action**

   * Open-source GitHub Action wrapper around `reviewgate-core`.
   * Lets teams run ReviewGate themselves in CI.
   * Acts as trust layer and top-of-funnel.

3. **Hosted ReviewGate App**

   * GitHub App that runs the same deterministic core (open-source
     codebase under Apache 2.0; optional paid hosting is an operations
     layer, not a license wall).
   * Runs automatically on PR open/update.
   * Posts reviewability comments.
   * Applies labels.
   * Sets status checks.
   * Supports `.reviewgate.yml`.
   * Adds hosted LLM reports, org policy management, audit logs, and
     paid enforcement features where product economics warrant them.

The public PR URL analyzer is useful, but it is not the main MVP. It is a later marketing artifact and acquisition tool.

The main MVP is the **GitHub enforcement loop**:

```text
PR opened or updated
→ ReviewGate analyzes reviewability
→ comment + labels + status check
→ author fixes PR before reviewers waste time
```

---

## 2. Product Thesis

AI-assisted coding increases code output, but it does not increase human review capacity. Teams are already seeing more PRs, larger diffs, weak descriptions, mixed concerns, and review fatigue.

ReviewGate exists because review is becoming the bottleneck.

The paid product is not “AI code review.” Existing tools already compete there.

ReviewGate owns the step before review:

> PR intake.

The product does not ask:

```text
Is this code correct?
```

It asks:

```text
Is this PR shaped well enough for a human reviewer to spend time on it?
```

This distinction matters.

A senior engineer may already know a PR is unreviewable, but saying that manually creates social friction. ReviewGate turns subjective reviewer frustration into neutral workflow enforcement.

Without enforcement, ReviewGate is advice.

With status checks, ReviewGate becomes a workflow gate.

---

## 3. Positioning

### Tagline

Make pull requests reviewable before humans waste time on them.

### Short pitch

ReviewGate is a PR intake gate for engineering teams. It flags oversized, unclear, risky, or mixed-scope pull requests before they reach human reviewers.

### Longer pitch

ReviewGate helps teams preserve code review quality as AI-assisted development increases PR volume. It checks size, scope, missing context, risky paths, and splitability, then posts a clear reviewability report directly on the PR.

### Enterprise-safe pitch

Enforce reviewability standards before pull requests reach senior reviewers.

### More aggressive founder-led pitch

AI tools doubled your PR volume. Your senior engineers’ review hours did not double. ReviewGate makes sure they spend those hours on PRs that are worth reviewing.

Alternative:

You already bought AI coding tools. ReviewGate keeps them from turning code review into garbage collection.

### What ReviewGate is

* PR intake checker
* reviewability gate
* pre-review quality tool
* reviewer-time protection
* GitHub workflow enforcement tool
* open-source rules engine with hosted enforcement

### What ReviewGate is not

* AI slop detector
* AI-origin detector
* full AI code reviewer
* security scanner
* bug finder
* Copilot replacement
* CI optimizer
* linter for code style
* test runner

### Language rule

Do not accuse authors of using AI. Do not label PRs as AI-generated. ReviewGate should not care whether a PR came from a human, Copilot, Cursor, Claude, Devin, or an internal agent.

It evaluates observable PR shape and reviewability only.

---

## 4. Product Layers

## 4.1 reviewgate-core

Open-source package that analyzes already-fetched PR metadata and changed files.

`reviewgate-core` must be a pure deterministic engine:

```text
(pr_metadata, changed_files, config) -> reviewability_report
```

Hard boundary rules:

* no GitHub API calls
* no network I/O
* no filesystem writes
* no database access
* no LLM calls
* no comments, labels, or status checks
* no side effects

Responsibilities:

* Validate normalized PR input.
* Categorize changed files.
* Compute raw LOC and post-exclusion ``human_loc_changed`` (§10.4).
* Detect risky paths.
* Detect weak or missing PR context.
* Detect large diffs.
* Detect suspicious mixed concerns.
* Generate deterministic warnings.
* Generate suggested labels.
* Compute baseline PASS/WARN/FAIL.
* Produce a normalized JSON report.

Non-responsibilities:

* Fetching GitHub data.
* Posting PR comments.
* Applying labels.
* Creating status checks.
* Calling LLM providers.
* Persisting analyses.
* Billing.
* Dashboards.

This boundary is load-bearing. The Action and hosted App are shells around the same pure engine. Keeping the core pure makes it easy to test, easy to trust, easy for the community to contribute rules, and easier to port to Go later if distribution as a single binary becomes important.

Preferred language:

* Python for fastest MVP implementation.

Alternative:

* Go later, if CLI distribution becomes a priority.

Initial recommendation:

* Build in Python first.
* Keep pure functions and strict schemas so a Go port is a translation, not a redesign.

## 4.2 reviewgate-action

Open-source GitHub Action wrapper around `reviewgate-core`.

Responsibilities:

* Run on pull requests.
* Read PR metadata from GitHub context.
* Fetch changed files.
* Load `.reviewgate.yml`.
* Run `reviewgate-core`.
* Print report in workflow logs.
* Optionally post a PR comment.
* Optionally fail the workflow based on threshold.

Purpose:

* Trust.
* Distribution.
* Developer adoption.
* Community rule contributions.
* Top-of-funnel for hosted App.

Limitations:

* Users self-host execution.
* Users maintain configuration.
* No org-level policy management.
* No hosted LLM report by default.
* No dashboard.
* No audit history.

## 4.3 Hosted ReviewGate GitHub App

Commercial product.

Responsibilities:

* Install on selected repos or org.
* Receive `pull_request` webhooks.
* Verify webhook signatures.
* Fetch PR metadata and changed files.
* Load `.reviewgate.yml` from repo.
* Normalize PR input for `reviewgate-core`.
* Run deterministic analysis through `reviewgate-core`.
* Optionally run hosted LLM report generation as a commercial hosted feature.
* Post PR comment.
* Apply labels.
* Set status check.
* Store analysis metadata and reports.
* Support basic org/repo settings.
* Support billing and plan limits later.

The hosted App owns all I/O, persistence, GitHub integration, queueing, LLM calls, comments, labels, and status checks.

This is the monetizable product.

## 4.4 Public PR URL Analyzer

Marketing tool, not core MVP.

Responsibilities:

* Let users paste a public GitHub PR URL.
* Generate a report.
* Capture email.
* Drive installs of hosted GitHub App.

Build after the GitHub App proves usefulness on real teams.

---

## 5. MVP Scope

The revised MVP is the hosted GitHub App plus open-source core and GitHub Action.

## 5.1 In scope for MVP

### Open-source core

* `reviewgate-core` package
* deterministic reviewability engine
* pure function boundary with zero I/O
* config schema
* file categorization
* ``human_loc_changed`` calculation (§10.4 exclusions)
* generated/lockfile/dependency/snapshot/minified file handling
* baseline PASS/WARN/FAIL
* JSON report schema
* CLI entrypoint for local fixture use

### GitHub Action

* GitHub Action wrapper
* run on PRs
* load `.reviewgate.yml`
* produce report
* optional workflow failure
* optional PR comment if token has permissions

### Hosted GitHub App

* GitHub App installation
* repository selection
* webhook receiver
* `pull_request.opened`, `pull_request.synchronize`, `pull_request.edited`, `pull_request.reopened`
* PR metadata fetch
* changed files fetch with pagination
* config fetch from `.reviewgate.yml`
* deterministic analysis
* optional LLM report generation
* PR comment creation/update
* label creation/application
* status check creation/update
* basic persistent storage
* basic rate limiting and caching
* private beta onboarding

### Minimal hosted UI

For private beta, keep hosted UI extremely small:

* landing page
* install or request-beta button
* installation success page
* privacy page

Do not build a repo list/status dashboard in MVP. For the first five friendly teams, onboarding and repo status can be handled through a short setup doc and direct founder support.

## 5.2 Out of scope for MVP

* Public polished PR URL analyzer
* repo list/status dashboard
* SaaS dashboard with charts
* billing automation
* GitHub Marketplace listing
* Slack integration
* Jira or Linear integration
* IDE plugin
* full AI code review
* bug detection
* security scanning
* auto-fixes
* test execution
* repository cloning
* persistent repo indexing
* AI-origin detection
* per-developer analytics
* advanced compliance reports

---

## 6. Success Criteria

The MVP is successful if teams install ReviewGate and let it influence PR workflow.

Primary validation metric:

```text
Teams asking for or enabling blocking status checks.
```

Secondary metrics:

* GitHub App installs
* active repositories
* PRs analyzed
* comments posted
* status checks created
* status checks set to WARN/FAIL
* PRs updated after ReviewGate warning
* teams customizing `.reviewgate.yml`
* teams asking for org-level config
* teams asking for paid plan

30-day private beta targets, assuming pre-warmed friendly teams:

```text
5 friendly teams installed
20 active repositories
200 PRs analyzed
50 ReviewGate comments posted
10 PRs changed after ReviewGate warning
3 teams enabling required status checks
1 team willing to pay $99/month
```

If beta teams are acquired cold, extend this validation window to 60–90 days. Installation, trust, and workflow enforcement require more time with cold teams.

The strongest validation signal:

> A team makes ReviewGate required before review or merge.

---

## 7. Target Users and Buyers

### Primary users

* Staff engineers
* Senior engineers
* Engineering managers
* Tech leads
* Platform engineers
* DevEx engineers

### Buyers

* Engineering managers
* Heads of Engineering
* VP Engineering
* CTOs
* Platform/DevEx leads

### Ideal customer profile

* 10 to 150 engineers
* GitHub-based workflow
* uses AI coding tools heavily
* senior engineers complain about review load
* review queues are growing
* large PRs are common
* PR descriptions are inconsistent
* team lacks consistent PR hygiene rules

### Pain statements

* “Our PRs are getting bigger.”
* “AI made people ship more code, but review got worse.”
* “Senior engineers are spending too much time asking for context.”
* “This should have been split before review.”
* “We need a neutral gate so it is not just one reviewer being picky.”

---

## 8. Core User Flows

## 8.1 GitHub App install flow

1. User visits ReviewGate landing page.
2. User clicks `Install ReviewGate` or `Join Beta`.
3. User installs GitHub App on selected repositories.
4. ReviewGate stores installation ID and selected repos.
5. User sees success page with setup instructions.
6. User optionally adds `.reviewgate.yml` to repo.
7. ReviewGate begins analyzing future PRs.

## 8.2 Pull request analysis flow

1. PR is opened or updated.
2. GitHub sends webhook to ReviewGate.
3. ReviewGate verifies webhook signature.
4. ReviewGate fetches PR metadata.
5. ReviewGate fetches changed files with pagination.
6. ReviewGate fetches `.reviewgate.yml` from base branch if present.
7. ReviewGate checks cache.
8. ReviewGate runs deterministic engine.
9. ReviewGate optionally runs LLM report generation.
10. ReviewGate creates or updates PR comment.
11. ReviewGate applies labels.
12. ReviewGate sets status check.
13. ReviewGate stores analysis metadata and report.

## 8.3 Author fix flow

1. ReviewGate marks PR as WARN or FAIL.
2. Author reads report.
3. Author adds missing context, links issue, splits PR, or explains risky files.
4. Author pushes update or edits PR body.
5. ReviewGate reruns.
6. Status changes to PASS or WARN.

## 8.4 Required status check flow

1. Team configures GitHub branch protection to require ReviewGate status check.
2. ReviewGate sets `reviewgate/reviewability` to PASS/WARN/FAIL.
3. Branch protection blocks merge on FAIL.
4. Team may configure whether WARN is blocking.

## 8.5 GitHub Action flow

1. User adds ReviewGate GitHub Action to workflow.
2. User optionally adds `.reviewgate.yml`.
3. Workflow runs on PR.
4. Action runs `reviewgate-core`.
5. Action prints report.
6. Action can fail job based on policy.

---

## 9. Reviewability Concepts

## 9.1 Reviewability statuses

### PASS

The PR appears reasonably reviewable.

Conditions:

* moderate size
* clear title/body
* linked issue or explicit context
* no major unexplained risky path changes
* scope appears coherent

PASS does not mean code is correct.

### WARN

The PR can be reviewed, but it creates avoidable reviewer burden.

Examples:

* slightly large diff
* weak PR body
* risky files touched but some explanation present
* missing tests for source changes
* multiple concerns but still plausibly related

### FAIL

The PR should not enter human review yet.

Examples:

* extremely large diff
* no meaningful PR description
* risky paths touched without rationale
* unrelated concerns mixed together
* likely needs splitting
* too many files to review efficiently

FAIL means “fix the shape of the PR before review,” not “the code is bad.”

---

## 10. Deterministic Engine Requirements

The deterministic engine is the foundation of trust. It must work without LLMs.

## 10.1 Inputs

```json
{
  "pr": {
    "title": "string",
    "body": "string",
    "author": "string",
    "base_branch": "string",
    "head_branch": "string",
    "additions": 0,
    "deletions": 0,
    "changed_files": 0
  },
  "files": [
    {
      "filename": "string",
      "status": "added|modified|removed|renamed",
      "additions": 0,
      "deletions": 0,
      "changes": 0,
      "patch": "optional string"
    }
  ],
  "config": {}
}
```

## 10.2 Outputs

```json
{
  "reviewability": "PASS|WARN|FAIL",
  "stats": {},
  "warnings": [],
  "suggested_labels": [],
  "file_categories": [],
  "split_hints": [],
  "reviewer_checklist": []
}
```

## 10.3 Default thresholds

```yaml
warn:
  files_changed: 25
  human_loc_changed: 800
  risky_files_changed: 2
  dependency_files_changed: 1
  config_files_changed: 1

fail:
  files_changed: 75
  human_loc_changed: 2500
  risky_files_without_context: 1
```

Important: use `human_loc_changed`, not raw LOC changed, for size severity.

## 10.4 Post-exclusion LOC (`human_loc_changed`)

Raw LOC can be misleading because lockfiles, generated files, snapshots,
vendored trees, and minified assets can dominate a diff. The JSON field is
still named ``human_loc_changed`` for stable thresholds and parsers; it
means **lines remaining after the categorizer exclusions below** (and
after any dependency-automation override per §10.4.1), not “typed by the
PR author.”

Compute (baseline before §10.4.1):

```text
raw_loc_changed = additions + deletions
excluded_loc_changed = lockfile + generated + vendored + minified + snapshot LOC
human_loc_changed = raw_loc_changed - excluded_loc_changed
```

Still report raw LOC, but base size severity primarily on
``human_loc_changed`` (post-exclusion LOC), not on raw totals.

Example:

```text
Raw LOC: 4,200
human_loc_changed: 350
Excluded: package-lock.json, snapshots
```

This should not automatically FAIL on size.

### 10.4.1 Dependency automation authors

When ``EngineInput.pr.author`` matches a small shipped allow-list (for
example ``dependabot[bot]``, ``renovate[bot]``, ``renovate-bot``) **and**
every changed file carries at least one of ``dependency`` or ``lockfile``
in its ``categories`` list while **no** file carries ``source``, the
engine replaces the §10.4 baseline with ``human_loc_changed = 0`` and
``excluded_loc_changed = raw_loc_changed`` so §10.3 size warnings do not
treat manifest churn as a large human diff.

The §10.2 ``stats`` map includes boolean ``dependency_automation_manifest_only``
when the override above applied. Mixed PRs (same author but ``source``
files present) keep the baseline §10.4 numbers. Author classification
(§10.4.2) is always emitted alongside this logic.

### 10.4.2 PR author login classification

The engine adds ``stats["pr_author_kind"]`` using only GitHub
``user.login`` string matching (no branch-name heuristics, no code
analysis). Values are a closed set:

* ``human`` — default when the login is empty or not matched below.
* ``dependency_automation`` — Dependabot / Renovate identities from the
  shipped allow-list (case-insensitive match).
* ``coding_agent_automation`` — known coding-agent or AI-integration App
  identities that open PRs (Copilot, Cursor, Codex connector, Claude App,
  Devin integration, …; case-insensitive match; extend via issues when new
  stable logins are confirmed).
* ``generic_automation`` — any other login ending in ``[bot]`` not in the
  two allow-lists (for example ``github-actions[bot]``).

When non-empty, ``stats["pr_author_login"]`` repeats the trimmed opener
login for convenience.

This answers “who opened the PR on GitHub?” It does **not** assert how
lines were written; §8 language rules for LLM copy still forbid accusing
authors of using AI or labelling code as AI-generated.

## 10.5 File categories

Each changed file may have multiple categories.

Categories:

* source
* test
* docs
* config
* dependency
* lockfile
* migration
* infra
* auth
* billing
* generated
* snapshot
* vendored
* minified
* asset
* unknown

Example:

```json
{
  "filename": "app/auth/session.ts",
  "categories": ["source", "auth"],
  "risky": true,
  "human_authored": true,
  "changes": 120
}
```

## 10.6 Risky path patterns

Default risky paths:

```yaml
risky_paths:
  - "**/migrations/**"
  - "**/migration/**"
  - "**/auth/**"
  - "**/authentication/**"
  - "**/billing/**"
  - "**/payments/**"
  - "**/infra/**"
  - "**/terraform/**"
  - "**/.github/workflows/**"
  - "Dockerfile"
  - "docker-compose.yml"
  - "compose.yml"
```

## 10.7 Dependency and lockfile patterns

```yaml
dependency_files:
  - "package.json"
  - "requirements.txt"
  - "pyproject.toml"
  - "poetry.lock"
  - "go.mod"
  - "Cargo.toml"

lockfiles:
  - "package-lock.json"
  - "pnpm-lock.yaml"
  - "yarn.lock"
  - "poetry.lock"
  - "uv.lock"
  - "go.sum"
  - "Cargo.lock"
```

## 10.8 Generated, vendored, minified, and snapshot patterns

```yaml
generated_paths:
  - "**/generated/**"
  - "**/gen/**"
  - "**/*.pb.go"
  - "**/*.generated.*"
  - "**/openapi.generated.*"

vendored_paths:
  - "vendor/**"
  - "third_party/**"
  - "node_modules/**"

minified_paths:
  - "**/*.min.js"
  - "**/*.min.css"

snapshot_paths:
  - "**/__snapshots__/**"
  - "**/*.snap"
```

These files count toward raw size and review burden, but should be
separated from the post-exclusion ``human_loc_changed`` total per §10.4.

## 10.9 Test path patterns

```yaml
test_paths:
  - "**/test/**"
  - "**/tests/**"
  - "**/__tests__/**"
  - "*.test.*"
  - "*.spec.*"
  - "test_*.py"
  - "*_test.go"
```

## 10.10 PR context checks

### Weak body

Warn if body is:

* empty
* whitespace
* fewer than 80 meaningful characters
* mostly template headings without content

### Missing linked issue

Warn if no issue/ticket reference appears in title or body.

Detect:

* `#123`
* `GH-123`
* `fixes #123`
* `closes #123`
* `resolves #123`
* external IDs like `ABC-123`
* URLs from Jira, Linear, GitHub Issues

### Missing rationale for risky paths

If risky files are touched and PR body does not mention why, warn or fail depending on severity.

## 10.11 Mixed concern detection

Do not simply fail because many categories are touched. A good feature PR may touch source, tests, docs, and config.

Detect **unrelated concern clusters** instead.

Normal combinations:

```text
source + tests
source + tests + docs
source + tests + config when config is directly related
migration + source + tests when body explains schema change
```

Suspicious combinations:

```text
billing + auth + infra
migration + workflow + unrelated UI refactor
dependency update + behavioral feature change
large refactor + business logic change
formatting-only churn + functional change
auth + unrelated docs rewrite + dependency bump
```

Initial implementation can use heuristic category combinations and file path clusters.

Do not overclaim semantic scope drift until linked issue and PR body comparison are stronger.

## 10.12 Warning schema

```json
{
  "code": "large_human_diff",
  "severity": "medium",
  "message": "This PR changes 1,200 lines after §10.4 exclusions (human_loc_changed), above the warning threshold of 800.",
  "evidence": {
    "human_loc_changed": 1200,
    "threshold": 800
  }
}
```

## 10.13 Baseline status aggregation

```python
def baseline_reviewability(warnings):
    high = sum(1 for w in warnings if w.severity == "high")
    medium = sum(1 for w in warnings if w.severity == "medium")

    if high >= 2:
        return "FAIL"
    if high == 1 and medium >= 1:
        return "FAIL"
    if high == 1 or medium >= 2:
        return "WARN"
    return "PASS"
```

---

## 11. Hosted LLM Reviewability Layer

The LLM layer belongs to the hosted App only.

It is not part of `reviewgate-core`.
It is not part of `reviewgate-action` in the MVP.

The open-source engine remains deterministic. Self-hosted users get the rules engine. Hosted users get the rules engine plus AI-generated explanations, split suggestions, reviewer checklists, no LLM key management, and no operational burden.

The hosted LLM layer improves explanation quality, split suggestions, and reviewer checklist generation.

It must not be required for the deterministic engine to work.

## 11.1 Hosted LLM responsibilities

* Turn deterministic warnings into a concise report.
* Suggest actionable fixes.
* Suggest smaller PR splits.
* Produce reviewer checklist.
* Explain reviewability burden professionally.

## 11.2 Hosted LLM non-responsibilities

* Full code review.
* Security review.
* Bug detection.
* AI-origin detection.
* Judging author skill.
* Claiming merge safety.

## 11.3 Structured output

Use provider-native structured output or tool use if available.

Do not rely on prompt-only JSON when schema enforcement is available.

Fallback order:

1. provider-native structured output
2. JSON parse
3. one repair attempt
4. deterministic-only report

## 11.4 Token and cost budget

Set explicit budgets.

Initial recommended model tier:

* default implementation setting: `REVIEWGATE_LLM_MODEL`
* initial default candidate: `gpt-4o-mini`, Claude Haiku-class, or equivalent low-cost structured-output-capable model
* pick one concrete model in implementation config and benchmark it against fixtures before beta
* premium/enterprise option later: stronger model for more nuanced reports

Per PR analysis target:

```text
small PR: under 4k input tokens
medium PR: under 8k input tokens
large PR: under 12k input tokens
huge PR: summary-only mode, no patches
```

Cost target:

```text
average LLM cost per analysis: under $0.02 if using mini/Haiku-class models
hard maximum per analysis: $0.20
```

Latency target:

```text
p50: under 8 seconds
p95: under 25 seconds
```

If estimated context exceeds budget, use summary-only mode or deterministic-only mode.

## 11.5 Data sent to LLM

Send:

* PR title
* PR body
* linked issue summary if available
* stats
* file category summary
* risky path list
* deterministic warnings
* compact file list

Avoid sending:

* full patches by default
* secrets
* private repo contents beyond what is necessary
* entire generated files
* huge lockfiles

## 11.6 Prompt

Store as:

```text
prompts/reviewability_v1.txt
```

Prompt:

```text
You are a senior staff engineer doing pull request intake triage.

Decide whether this pull request is reviewable by a human right now.

Do not review code correctness.
Do not claim the code is correct, incorrect, secure, insecure, or production-ready.
Do not mention AI, Copilot, Cursor, Claude, agents, or generated code.
Do not speculate about the author.
Evaluate only reviewability, scope clarity, missing context, risky paths, and reviewer burden.

Use the provided deterministic warnings as primary evidence.
Never invent files, issues, or facts.
```

Use provider-native structured output with the schema in Section 11.7. If falling back to plain JSON prompting, include the full schema in the prompt template.

## 11.7 Report schema

```json
{
  "reviewability": "PASS|WARN|FAIL",
  "summary": "short paragraph",
  "issues": [
    {
      "severity": "low|medium|high",
      "title": "short issue title",
      "evidence": "specific evidence",
      "suggested_fix": "specific action"
    }
  ],
  "suggested_labels": ["string"],
  "split_suggestions": [
    {
      "title": "smaller PR title",
      "scope": "what belongs in it"
    }
  ],
  "reviewer_checklist": ["string"]
}
```

## 11.8 Verdict control

The deterministic engine owns the baseline verdict.

The hosted LLM may escalate severity:

```text
PASS -> WARN
WARN -> FAIL
```

The hosted LLM must not downgrade deterministic severity:

```text
FAIL -> WARN is not allowed
WARN -> PASS is not allowed
```

Final verdict rule:

```text
final_reviewability = max_severity(deterministic_baseline, llm_verdict)
```

The status check, labels, and report header must all use `final_reviewability`.

Reason: objective policy violations must remain stable. The LLM can improve language and add explanation, but it cannot override deterministic enforcement.

---

## 12. `.reviewgate.yml` Configuration

Repos can customize behavior.

Default file path:

```text
.reviewgate.yml
```

Example:

```yaml
version: 1

mode: app # app | action | both

llm_reports: false # default false for private repos unless explicitly enabled during beta onboarding

# mode behavior:
# app: hosted GitHub App posts comments/checks. Action should stay quiet.
# action: GitHub Action posts comments/checks. Hosted App should not post.
# both: both are allowed, using distinct check names.

thresholds:
  warn:
    files_changed: 25
    human_loc_changed: 800
    risky_files_changed: 2
    dependency_files_changed: 1
    config_files_changed: 1
  fail:
    files_changed: 75
    human_loc_changed: 2500

policy:
  require_linked_issue: true
  require_human_summary: true
  fail_on_risky_paths_without_context: true
  fail_on_huge_pr: true
  warn_blocks_merge: false

risky_paths:
  - "**/migrations/**"
  - "**/auth/**"
  - "**/billing/**"
  - "**/infra/**"
  - ".github/workflows/**"

ignored_paths:
  - "**/*.snap"
  - "**/generated/**"

labels:
  pass: "reviewability-pass"
  warn: "reviewability-warn"
  fail: "reviewability-fail"
  too_large: "too-large"
  missing_context: "missing-context"
  risky_change: "risky-change"
  needs_split: "needs-split"
  needs-tests: "needs-tests"
  dependency-change: "dependency-change"
  config-change: "config-change"

status_check:
  enabled: true
  name: "reviewgate/reviewability"
  fail_on: "FAIL"
  warn_blocks_merge: false
```

### 12.1 Shipped engine and hosted behaviour (GitHub #124)

The open-source engine and hosted worker implement the following (see GitHub issue #124 for the alignment audit):

* **`ignored_paths`** — Glob matches are removed before categorisation and stats; PR-level `additions` / `deletions` from GitHub are **not** rewritten unless at least one path was ignored (per-file sums are only used after filtering).
* **`thresholds.warn` extras** — `risky_files_changed`, `dependency_files_changed`, and `config_files_changed` emit additional `medium` warnings when counts reach the configured thresholds. The shipped default for `risky_files_changed` is **2** so a lone risky file still relies on the rationale heuristic instead of duplicating signal.
* **`policy.require_human_summary`** — When `false`, the weak-PR-body heuristic is skipped entirely (linked-issue policy is unchanged).
* **`policy.fail_on_huge_pr`** — When `false`, the §22.3 **>1000 files** fail-fast path yields `WARN` with a `medium` severity marker instead of `FAIL` / `high`.
* **`status_check.fail_on`** — Maps to GitHub check conclusions: verdicts at or above this tier publish `failure`; `WARN` below that tier stays `neutral` unless `warn_blocks_merge` forces `failure`.
* **§13.9 labels** — `needs-tests`, `dependency-change`, and `config-change` are configurable label names; deterministic warnings map to them when present.
* **Hosted purge** — `purge_analyses_for_old_uninstalls` (Dramatiq) deletes `analyses` / `analysis_reports` rows for installations whose `deleted_at` is older than 30 days, complementing `purge_old_webhook_deliveries`.

Config precedence:

1. repo `.reviewgate.yml`
2. org-level hosted config, future
3. ReviewGate defaults

For MVP, implement repo config and defaults only.

The status check name is configurable because teams may encode it in branch protection rules. Default to `reviewgate/reviewability` and treat that name as stable unless the repo explicitly changes it.

Malformed config behavior:

* Do not crash analysis.
* Run with default config.
* Add a config warning to the ReviewGate comment.
* Set the status check based on the PR analysis unless the config error itself prevents safe execution.

Example warning:

```text
ReviewGate could not parse .reviewgate.yml: <error>. Running with defaults.
```

---

## 13. GitHub App Technical Design

## 13.1 App permissions

Required repository permissions:

```text
Pull requests: read
Contents: read
Metadata: read
Issues: read
Checks: read/write
Commit statuses: read/write, optional depending implementation
Issues/PR comments: read/write
Labels: read/write
```

Prefer Checks API for status checks.

## 13.2 Webhook events

Subscribe to:

```text
pull_request.opened
pull_request.synchronize
pull_request.edited
pull_request.reopened
```

For `pull_request.edited`, inspect the webhook `changes` payload before enqueueing analysis.

Rerun only if one of these changed:

```text
changes.title
changes.body
changes.base
```

Skip analysis for edited events that only change assignees, reviewers, labels, milestones, or other non-reviewability inputs.

Also subscribe to:

```text
installation.created
installation.deleted
installation_repositories.added
installation_repositories.removed
```

`installation.deleted` is required in MVP. When received, mark the installation as deleted, stop processing future jobs for that installation, and apply the beta data-retention policy.

Optional later:

```text
check_suite.rerequested
```

## 13.3 Webhook receiver

Endpoint:

```http
POST /webhooks/github
```

Requirements:

* Verify `X-Hub-Signature-256`.
* Reject invalid signatures.
* Deduplicate delivery IDs.
* Return 2xx quickly.
* Enqueue analysis job.

## 13.4 Authentication

Use GitHub App server-to-server authentication.

Do not rely on a single personal access token.

Reason:

* better trust story
* higher rate limits per installation
* correct permission model
* easier org adoption

Store:

* app ID
* private key
* webhook secret
* installation IDs

## 13.5 PR data fetch

Fetch:

```http
GET /repos/{owner}/{repo}/pulls/{pull_number}
GET /repos/{owner}/{repo}/pulls/{pull_number}/files?per_page=100&page=N
GET /repos/{owner}/{repo}/contents/.reviewgate.yml?ref={base_sha_or_branch}
```

Handle pagination fully.

Do not cap at 300 files by default. For huge PRs, enter summary-only mode and likely fail as unreviewably large.

Suggested behavior:

```text
0-300 files: normal analysis
301-1000 files: summary-only analysis, likely FAIL
1000+ files: fail fast as unreviewably large, no LLM
```

## 13.6 Caching

Cache analysis by:

```text
repository_id + pull_number + head_sha + config_hash + pr_metadata_hash
```

`pr_metadata_hash` must include at least:

```text
normalized PR title
normalized PR body
sorted linked issue references
base branch
```

Normalization rules:

```text
normalize line endings to LF
strip leading/trailing whitespace
collapse repeated whitespace where semantics are unaffected
strip HTML comments
sort linked issue references before hashing
use empty string for missing fields
```

Reason: a PR body edit can fix missing context without changing `head_sha`. ReviewGate must rerun when the author improves the description, but it should not rerun for meaningless whitespace or HTML-comment-only changes.

If the same PR head SHA, config, and metadata hash were already analyzed, reuse result.

Cache benefits:

* lower GitHub API usage
* lower LLM cost
* faster rerenders
* safer burst handling

Do not cache intermediate GitHub API responses unless the cache key includes `repo + pr + head_sha`. Incorrectly caching `/pulls/{number}` or changed files by PR number alone can analyze stale data after force-pushes.

Default MVP stance:

```text
cache final analysis results only
avoid intermediate GitHub API caching unless keyed by head_sha
```

TTL:

```text
24 hours for analysis results
no intermediate GitHub API cache by default
```

## 13.7 Queue and idempotency

Use background jobs for webhook analysis.

Recommended:

* Dramatiq + Redis, or
* Celery + Redis

Avoid RQ for MVP because webhook-driven products need retries with backoff, delayed jobs, and better behavior under API/LLM rate limits.

Webhook should not block on LLM.

Idempotency has three separate mechanisms:

1. Delivery dedupe

   * Deduplicate GitHub delivery IDs.
   * Store `X-GitHub-Delivery` in `webhook_deliveries`.

2. Enqueue dedupe

   * Before enqueueing, compute or approximate the analysis key.
   * If an analysis row already exists for `repository_id + pull_number + head_sha + config_hash + pr_metadata_hash` with `status = completed`, skip enqueue.
   * Apply a short debounce window, such as 30 seconds, for rapid `synchronize` bursts from force-pushes or rebases.

3. Worker lock

   * The worker must acquire a Redis lock on `repository_id + pull_number + head_sha + config_hash + pr_metadata_hash` for the duration of the job.
   * If the lock is already held, exit quietly.
   * This prevents duplicate comments, duplicate status updates, and duplicate LLM calls.

## 13.8 Comment behavior

ReviewGate should create one persistent PR comment and update it on subsequent runs.

Use hidden marker:

```html
<!-- reviewgate-report -->
```

On rerun:

1. Search existing PR comments.
2. Filter by the ReviewGate App bot user, usually `{app_slug}[bot]`.
3. Filter by the hidden marker.
4. Update that comment.
5. If absent, create a new comment.

Do not update a comment that only contains the marker but was written by a human or another bot.

## 13.9 Label behavior

Labels to apply:

* `reviewability-pass`
* `reviewability-warn`
* `reviewability-fail`
* `too-large`
* `missing-context`
* `risky-change`
* `needs-split`
* `needs-tests`
* `dependency-change`
* `config-change`

On rerun:

* Remove stale ReviewGate labels.
* Apply current labels.

Do not remove user labels.

## 13.10 Status check behavior

Create check run:

```text
name: reviewgate/reviewability by default, configurable in .reviewgate.yml
conclusion:
  PASS -> success
  WARN -> neutral by default
  FAIL -> failure
```

GitHub Checks API has no `success with warning` conclusion. Use `neutral` for WARN by default so it is visible without blocking merge.

Default:

```text
PASS = success
WARN = neutral
FAIL = failure
```

If `warn_blocks_merge: true`, map WARN to `failure`.

The status check is the load-bearing MVP feature. Comments explain the result, but the check run is what creates workflow enforcement and future monetization.

Teams can configure WARN as blocking later.

---

## 14. GitHub Action Design

Repository (standalone split, future):

```text
github.com/reviewgate/reviewgate-action
```

**Open-source monorepo (today).** Until that repository exists, the same
Action ships from the root `action.yml` in
[`leo-aa88/reviewgate`](https://github.com/leo-aa88/reviewgate). Consumers
reference `leo-aa88/reviewgate@<tag>` (for example `@v0.1.0` after the
first public release; pre-release docs may use `@main`).

Usage:

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
      - uses: leo-aa88/reviewgate@main
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          fail-on: FAIL
          post-comment: true
```

Action responsibilities:

* Fetch PR metadata and files.
* Load `.reviewgate.yml`.
* Read `mode` from config.
* Run core.
* Output summary.
* Optionally comment.
* Exit non-zero if policy requires.

Action inputs:

```yaml
github-token: required
fail-on: FAIL
post-comment: true
mode: auto # auto | action | quiet
```

`auto` should read `.reviewgate.yml` and stay quiet when `mode: app`.

## 14.1 Action and hosted App coexistence

Teams may install the hosted App after trying the Action. Avoid double comments and duplicate status checks.

Use `.reviewgate.yml` field:

```yaml
mode: app # app | action | both
```

Behavior:

```text
mode: app
  Hosted App posts comments/checks.
  GitHub Action should run in quiet/no-op mode if present.

mode: action
  GitHub Action posts comments/checks.
  Hosted App should not post comments/checks.

mode: both
  Both may run, but must use distinct status check names.
```

Default mode for hosted App installs:

```text
app
```

Commercial upsell:

```text
Want hosted LLM reports, org-wide config, audit logs, and no workflow maintenance? Use the hosted ReviewGate App.
```

---

## 15. Backend Architecture

Recommended stack:

```text
Python 3.12+
FastAPI
Postgres
Redis / Upstash Redis
Dramatiq or Celery
SQLAlchemy or SQLModel
Pydantic
httpx
GitHub App auth library or custom JWT
OpenAI or Anthropic for LLM
```

High-level architecture:

```text
GitHub Webhook
  ↓
FastAPI webhook receiver
  ↓
Queue job
  ↓
GitHub API client
  ↓
Config loader
  ↓
reviewgate-core
  ↓
LLM report layer, optional
  ↓
Postgres persistence
  ↓
GitHub comment + labels + status check
```

Module structure:

```text
reviewgate/
  core/
    categorizer.py
    heuristics.py
    config.py
    schemas.py
    report.py
    cli.py
  app/
    main.py
    settings.py
    webhooks/
      github.py
    github/
      auth.py
      client.py
      comments.py
      checks.py
      labels.py
      parser.py
    analysis/
      pipeline.py
      cache.py
      jobs.py
    llm/
      client.py
      prompts.py
      schemas.py
    storage/
      models.py
      repositories.py
      db.py
    rate_limit/
      limiter.py
  prompts/
    reviewability_v1.txt
```

---

## 16. Database Design

Use persistence from day one. It is needed for validation, caching, reports, beta tracking, and future billing.

Do not store full patches by default.

## 16.1 Tables

### installations

```sql
create table installations (
  id uuid primary key default gen_random_uuid(),
  github_installation_id bigint not null unique,
  account_login text not null,
  account_type text not null,
  created_at timestamptz not null default now(),
  deleted_at timestamptz
);
```

### repositories

```sql
create table repositories (
  id uuid primary key default gen_random_uuid(),
  installation_id uuid references installations(id),
  github_repository_id bigint not null unique,
  owner text not null,
  name text not null,
  full_name text not null,
  private boolean not null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);
```

### analyses

```sql
create table analyses (
  id uuid primary key default gen_random_uuid(),
  repository_id uuid references repositories(id),
  pull_number integer not null,
  head_sha text not null,
  config_hash text not null,
  pr_metadata_hash text not null,
  status text not null,
  reviewability text,
  check_run_id bigint,
  check_run_name text,
  files_changed integer,
  raw_loc_changed integer,
  human_loc_changed integer,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  error_code text,
  unique(repository_id, pull_number, head_sha, config_hash, pr_metadata_hash)
);

create index idx_analyses_repo_pr on analyses(repository_id, pull_number);
create index idx_analyses_created_at on analyses(created_at);
```

### analysis_reports

```sql
create table analysis_reports (
  id uuid primary key default gen_random_uuid(),
  analysis_id uuid references analyses(id),
  report_json jsonb not null,
  deterministic_json jsonb not null,
  llm_used boolean not null default false,
  llm_provider text,
  input_tokens integer,
  output_tokens integer,
  estimated_cost_usd numeric(10,4),
  created_at timestamptz not null default now()
);
```

### beta_leads

```sql
create table beta_leads (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  name text,
  company text,
  role text,
  github_org text,
  team_size text,
  source text,
  created_at timestamptz not null default now()
);
```

### webhook_deliveries

```sql
create table webhook_deliveries (
  id uuid primary key default gen_random_uuid(),
  github_delivery_id text not null unique,
  event_name text not null,
  processed boolean not null default false,
  created_at timestamptz not null default now()
);

create index idx_webhook_deliveries_created_at on webhook_deliveries(created_at);

-- Cleanup job requirement:
-- delete webhook_deliveries older than 30 days daily.
```

---

## 17. API Endpoints

## 17.1 GitHub webhook

```http
POST /webhooks/github
```

No public response body needed beyond success.

## 17.2 Health

```http
GET /health
```

Response:

```json
{
  "ok": true
}
```

## 17.3 Beta lead capture

```http
POST /api/beta-leads
```

Request:

```json
{
  "email": "user@example.com",
  "name": "Optional",
  "company": "Optional",
  "role": "Optional",
  "github_org": "Optional",
  "team_size": "10-50",
  "source": "landing"
}
```

Response:

```json
{
  "ok": true
}
```

## 17.4 Future public PR analyzer, not MVP

```http
POST /api/analyze-public-pr
```

Implement later.

---

## 18. Report Comment Format

The PR comment should be concise and actionable.

Example:

```markdown
<!-- reviewgate-report -->

## ReviewGate: WARN

This PR may be difficult to review because it changes 42 files, touches risky paths, and does not clearly explain why migration and authentication files are included.

### Issues found

1. **Large post-exclusion diff**
   - Evidence: 1,200 ``human_loc_changed`` across 42 files.
   - Suggested fix: Split unrelated areas into smaller PRs.

2. **Risky paths touched without enough rationale**
   - Evidence: `migrations/` and `auth/` files changed.
   - Suggested fix: Add a PR section explaining the expected behavior and migration risk.

3. **Missing linked issue or acceptance criteria**
   - Evidence: No issue reference or acceptance criteria found in the PR body.
   - Suggested fix: Link the ticket or add clear acceptance criteria.

### Suggested labels

`reviewability-warn` `too-large` `risky-change` `missing-context` `needs-split`

### Suggested split

1. Database migration and schema changes
2. Authentication behavior changes
3. Test coverage and cleanup

### Reviewer checklist

- Confirm the migration is backwards compatible.
- Confirm the auth behavior change is intentional.
- Confirm tests map to the stated acceptance criteria.

_ReviewGate evaluates reviewability, not code correctness._
```

Tone rules:

Use:

* “This PR may be difficult to review because...”
* “Consider splitting...”
* “Add context...”
* “Risky paths touched without explanation...”

Avoid:

* “This PR is bad.”
* “This is AI slop.”
* “The author failed...”
* “The code is unsafe.”
* “Reject this PR.”

---

## 19. Open-Source Strategy

Open-source is part of distribution and trust.

## 19.1 Open-source repositories

### reviewgate-core

Contains:

* deterministic engine
* config schema
* path pattern lists
* thresholds
* report schemas
* CLI
* tests

### reviewgate-action

Contains:

* GitHub Action wrapper
* examples
* docs

## 19.2 Hosted service code (open source)

The GitHub App backend, queueing, persistence, hosted LLM integration,
billing hooks, org policy UI, and audit logging are **part of the same
open-source product** as `reviewgate-core` and `reviewgate-action`.
They may ship in this monorepo or in a sibling repository under the
same GitHub org for deployment and release cadence, but they are **not**
a proprietary fork. Remaining MVP implementation is tracked in public
GitHub issues (from issue #28 onward in `leo-aa88/reviewgate`).

## 19.3 Why open-source core

Reasons:

* senior engineers trust inspectable rule engines
* community can improve patterns
* transparent roadmap and code build trust with adopters
* creates adoption path through GitHub Action
* hosted offerings can still monetize convenience, SLAs, and scale
  without hiding the implementation

## 19.4 Commercial differentiation (not closed source)

Revenue, where used, comes from **hosted operation**, support, and
productized convenience — not from withholding source. Typical paid
value adds include:

* managed GitHub App hosting and onboarding
* no-maintenance setup for large orgs
* LLM-generated reports backed by ReviewGate-operated keys and quotas
* org-level policy management at scale
* audit logs and retention guarantees
* hosted history and analytics
* enterprise support
* SSO / VPC / self-hosted support packages later

All of the above can ship as Apache 2.0 (or the same license as this
repo) while billing for the service layer.

---

## 20. Monetization Model

Initial monetization is hosted enforcement.

Free/open:

```text
reviewgate-core
reviewgate-action
basic deterministic checks
self-hosted config
```

Paid:

```text
hosted GitHub App
status checks
custom policies at scale
LLM reports
org-level config
audit logs
support
```

Private beta pricing:

```text
Free during private beta.
Target first paid plan: $99/month for small teams that want hosted status checks and custom policy enforcement.
Final tier details should be decided after beta usage and objections.
```

Do not overfit pricing before validation. Detailed tiers can live in a separate pricing experiments document.

Possible future pricing draft:

```text
Free hosted beta
- 1 repo
- 50 PR checks/month
- comments only

Team: $99/month
- 10 repos
- 1,000 PR checks/month
- comments
- labels
- status checks
- .reviewgate.yml

Growth: $299/month
- 50 repos
- 5,000 PR checks/month
- org-level config
- split suggestions
- report history

Business: $799/month
- 200 repos
- audit logs
- policy packs
- priority support

Enterprise: custom
- SSO
- custom retention
- self-hosted/VPC option
- SLA
```

Conversion trigger:

> “Can we make this required?”

That is the paid moment.

---

## 21. Privacy and Security

## 21.1 Core principles

* Do not clone repositories.
* Do not execute code.
* Do not run tests.
* Do not persist full patches by default.
* Do not expose GitHub or LLM credentials to frontend.
* Verify webhook signatures.
* Use least-privilege GitHub permissions.
* Store only metadata, reports, and analysis stats.

## 21.2 Data stored

Store:

* installation ID
* repository metadata
* PR number
* head SHA
* config hash
* PR metadata hash
* report JSON
* deterministic stats
* warnings
* labels
* token/cost metadata

Do not store by default:

* full file patches
* full repository contents
* secrets
* cloned code

Exception for future opt-in feature:

* small diff hunks for risky files may be stored only if explicitly enabled by repo/org config
* default remains no full patch persistence

## 21.3 LLM data handling

For private repos, LLM usage must be disclosed clearly before installation.

MVP modes:

```text
deterministic-only mode
  No PR content is sent to an LLM provider.

hosted LLM mode
  Compact PR metadata, file summaries, deterministic warnings, and limited context are sent to the configured LLM provider.
```

Implementation default:

```yaml
llm_reports: false
```

LLM reports are opt-in per repo. For friendly beta teams, the founder may enable LLM reports during onboarding after explicit confirmation.

Default beta stance:

```text
LLM reports are optional per repo.
Deterministic-only mode is always available.
ReviewGate does not send full patches by default.
```

Future enterprise:

* bring-your-own-key
* no-retention model settings
* self-hosted/VPC

## 21.4 Privacy copy

```text
ReviewGate evaluates pull request metadata, changed file paths, and compact diff summaries. It does not clone repositories, execute code, or persist full repository contents by default.
```

---

## 22. Rate Limits, Cost, and Reliability

## 22.1 GitHub API limits

Use GitHub App installation tokens, not a single PAT.

Benefits:

* higher rate limits
* per-installation isolation
* better permissions

## 22.2 Internal rate limits

Use Redis/Upstash from day one.

Limits during beta:

```text
per installation: 500 analyses/day
per repo: 100 analyses/day
per PR/head SHA/config: cached
```

## 22.3 Huge PR handling

Behavior:

```text
0-300 files: normal analysis
301-1000 files: summary-only, likely FAIL
1000+ files: fail fast, no LLM
```

Message:

```text
This PR changes more than 1000 files. ReviewGate considers it unreviewably large for normal human review. Split or narrow the PR before review.
```

## 22.4 LLM cost controls

* use deterministic-only for huge PRs
* cap file summaries
* exclude lockfiles/generated files from LLM context
* cache by head SHA
* track tokens and cost
* disable LLM per repo if needed

---

## 23. Analytics and Funnel Tracking

Track funnel per installation and repository.

Events:

```text
installation_created
repository_enabled
pr_webhook_received
analysis_started
analysis_completed
comment_posted
labels_applied
status_check_created
status_check_failed
status_check_warned
pr_updated_after_warning
config_file_detected
config_file_changed
beta_lead_submitted
```

Important funnel:

```text
installation → active repo → PR analyzed → warning produced → author updates PR → status improves → team enables required check
```

This is more important than landing page traffic.

---

## 23.1 Uninstall and data deletion

Handle `installation.deleted` in MVP.

On uninstall:

* mark installation `deleted_at`
* mark repositories inactive
* stop queued jobs for that installation if possible
* ignore future webhooks for that installation
* retain analysis metadata during beta only as described in privacy policy
* support manual deletion request by email during beta

Future production should include self-serve data deletion and retention controls.

Default beta retention policy after uninstall:

```text
Delete all analysis data for the installation within 30 days.
Support immediate manual deletion by email request.
```

The 30-day window is enforced by scheduled purge jobs over hosted tables: operators
run ``purge_old_webhook_deliveries`` (issue #34) and
``purge_analyses_for_old_uninstalls`` (GitHub #124) on a daily cadence alongside
their Dramatiq worker / cron infrastructure.

Privacy copy should state:

```text
If you uninstall ReviewGate, we delete analysis data associated with your installation within 30 days unless you request deletion sooner.
```

---

## 24. Testing Plan

## 24.1 Unit tests

Test:

* config parsing
* path matching
* file categorization
* human LOC calculation
* generated file exclusion
* lockfile handling
* weak PR body detection
* linked issue detection
* risky path detection
* mixed concern detection
* baseline status aggregation
* report schema validation

## 24.2 Fixtures

Create fixtures for:

1. small clean PR
2. large human-authored PR
3. large lockfile-only PR
4. generated code PR
5. snapshot-heavy PR
6. risky migration PR
7. auth change without context
8. dependency update only
9. dependency update plus behavior change
10. source + tests + docs normal feature
11. billing + auth + infra suspicious mixed concern
12. massive refactor
13. docs-only PR
14. test-only PR

## 24.3 Integration tests

Test with GitHub API mocked.

Scenarios:

* webhook signature valid
* webhook signature invalid
* duplicate delivery ID
* PR opened
* PR synchronized
* config exists
* config missing
* comment created
* comment updated
* labels applied
* status check set

## 24.4 Manual beta QA

Before each beta install:

* install GitHub App on test repo
* open small PR
* open large PR
* edit PR body
* push update
* confirm comment updates
* confirm labels update
* confirm status changes
* confirm no duplicate comments

---

## 25. Implementation Milestones

Human-facing setup artifacts should be created alongside the technical milestones:

* onboarding docs by Milestone 3
* privacy copy by Milestone 4
* beta feedback form by Milestone 6

## Milestone 1: reviewgate-core skeleton

Acceptance criteria:

* package exists
* schemas defined
* config loader works
* CLI accepts fixture JSON
* tests run

## Milestone 2: deterministic engine

Acceptance criteria:

* categorizes files
* computes raw and human LOC
* detects weak body
* detects missing issue
* detects risky paths
* detects generated/lockfile/snapshot files
* produces PASS/WARN/FAIL

## Milestone 3: GitHub Action

Acceptance criteria:

* action runs on PR
* fetches PR files
* runs core
* prints report
* can fail on FAIL

## Milestone 4: GitHub App skeleton

Acceptance criteria:

* app can be installed on test repo
* webhook receiver verifies signature
* delivery dedupe works
* jobs enqueue

## Milestone 5: GitHub App analysis

Acceptance criteria:

* fetches PR metadata
* fetches changed files with pagination
* loads `.reviewgate.yml`
* runs core
* stores analysis

## Milestone 6: PR output

Acceptance criteria:

* creates/updates PR comment
* applies labels
* sets check run
* no duplicate comments

## Milestone 7: LLM report layer

Acceptance criteria:

* structured output works
* token budget enforced
* deterministic fallback works
* cost metadata stored

## Milestone 8: Private beta readiness

Acceptance criteria:

* 5 test repos installed
* 20 test PRs analyzed
* status checks working
* comments concise and non-duplicative
* labels refreshed correctly
* privacy copy already reviewed
* onboarding instructions already tested
* feedback form already tested

## Milestone 9: Public demo, later

Acceptance criteria:

* public PR URL analyzer exists
* uses same core engine
* captures email
* points to GitHub App install

---

## 26. Agent Build Instructions

If an AI coding agent builds this project, follow these constraints:

1. Build `reviewgate-core` first.
2. Build deterministic analysis before LLM.
3. Build GitHub Action before hosted App if useful for testing.
4. Build hosted GitHub App as the main MVP.
5. Do not build the public PR URL analyzer first.
6. Do not build dashboard first.
7. Do not add billing in MVP.
8. Do not build Slack/Jira integrations.
9. Do not clone repositories.
10. Do not run code from PRs.
11. Do not store full diffs by default.
12. Do not label PRs as AI-generated.
13. Do not claim correctness or safety.
14. Use GitHub App auth, not a single PAT.
15. Use Redis caching/rate limiting from day one.
16. Use provider-native structured LLM output if available.
17. Keep LLM optional and fallback-safe.
18. Keep comments concise and evidence-based.
19. Keep ReviewGate labels namespaced and removable.
20. Prioritize status check enforcement, because that is the monetization switch.
21. Keep `reviewgate-core` pure: no network, no database, no filesystem writes, no LLM, no GitHub API.
22. Keep the hosted LLM layer outside the open-source core and Action for MVP.
23. Include PR metadata hash in analysis cache keys.
24. Handle uninstall webhooks in MVP.
25. Avoid duplicate App/Action output via `.reviewgate.yml` mode.

---

## 27. Definition of Done for MVP

The MVP is done when:

* `reviewgate-core` is open-source and tested.
* GitHub Action can run ReviewGate on PRs.
* Hosted GitHub App installs on selected repos.
* Webhooks are verified and processed.
* PR metadata and files are fetched correctly.
* `.reviewgate.yml` is supported.
* Deterministic analysis works without LLM.
* LLM report works when enabled.
* PR comment is created and updated.
* Labels are applied and refreshed.
* Status check is set.
* Analysis is cached by head SHA/config hash.
* Reports are persisted without storing full patches.
* At least 5 friendly teams can use it in private beta.

---

## 28. Future Public PR Analyzer

After the GitHub App proves useful, build the public analyzer as a marketing tool.

Flow:

```text
Paste public GitHub PR URL
→ ReviewGate report
→ email capture
→ install hosted App
```

This should reuse:

* `reviewgate-core`
* same report schema
* same LLM layer
* same UI components if available

Do not treat public analyzer traffic as proof of willingness to pay. It is top-of-funnel only.

---

## 29. First Marketing Use Case

After private beta works, post examples like:

```text
I built ReviewGate, a PR intake gate.

It does not review code correctness.
It asks whether a PR is ready for human review.

A beta repo opened a PR with:
- 58 files changed
- 2,400 raw LOC
- 1,700 ``human_loc_changed`` (post-exclusion LOC)
- migrations and GitHub workflows touched
- no linked issue
- no acceptance criteria

ReviewGate marked it FAIL and suggested splitting it into:
1. schema changes
2. workflow changes
3. application logic
4. tests

The author updated the PR before review.

That is the point: protect reviewer time before review starts.
```

---

## 30. Key Product Principle

The public demo is not the product.

The dashboard is not the product.

The product is the enforcement loop inside GitHub:

```text
PR opened
→ ReviewGate checks reviewability
→ ReviewGate comments and sets status
→ unreviewable PRs are fixed before human review
```

The open-source engine creates trust.

The hosted App creates convenience.

The status check creates monetization.
