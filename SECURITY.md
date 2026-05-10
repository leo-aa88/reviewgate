# Security policy

## Supported versions

ReviewGate is in private beta. Only the latest tagged release of
`reviewgate-core` and `reviewgate-action` from the
[`leo-aa88/reviewgate`](https://github.com/leo-aa88/reviewgate)
default branch (`main`) receives security fixes. Older tags are
not supported.

| Package | Supported version |
| ------- | ----------------- |
| `reviewgate` on PyPI (installs CLI `reviewgate-core`) | latest released minor |
| `reviewgate-action` (GitHub Action at `src/reviewgate_action/`) | latest `v1.x` tag |

## Reporting a vulnerability

**Please do not file a public issue or pull request describing a
suspected vulnerability.** Use one of the private channels below so we
can fix it before disclosure.

1. **GitHub private vulnerability reporting (preferred).**
   Open <https://github.com/leo-aa88/reviewgate/security/advisories/new>
   and submit an advisory. GitHub keeps the report private until the
   maintainers and you agree to publish.
2. **Email.** Send a description and (if possible) a reproducer to
   `46436462+leo-aa88@users.noreply.github.com` with the subject line
   prefixed `[ReviewGate security]`. Encrypt with the maintainer's
   GitHub-published GPG key if your report contains exploit details.

Please include:

* a description of the issue and the affected component
  (`reviewgate-core` engine, `reviewgate-action` runtime, hosted App
  worker, or other published component),
* the version, commit SHA, or release tag where you reproduced it,
* a minimal reproducer (ideally an `EngineInput` JSON fixture or the
  exact GitHub Action workflow input),
* the impact you observed (e.g. unbounded resource use, secret leak,
  RCE in the Action runner) and a CVSS score guess if you have one,
* whether you have already disclosed the issue elsewhere.

## What to expect

| Step | Target |
| ---- | ------ |
| Acknowledgement of your report | within **3 business days** |
| Initial triage and severity assessment | within **7 business days** |
| Fix or mitigation merged on `main` | within **30 days** for high or critical severity, **90 days** for medium and below |
| CVE request (when applicable) and coordinated disclosure | within **90 days** of the initial report unless we agree on a longer embargo |

We follow [coordinated vulnerability disclosure](https://www.cisa.gov/coordinated-vulnerability-disclosure-process).
We will credit you in the released advisory unless you ask to remain
anonymous. ReviewGate does not currently run a paid bug bounty.

## Threat model and scope

The deterministic engine in `reviewgate-core` is a **pure function**
(no network, no filesystem writes, no database, no LLM, no GitHub API
— see [`docs/DESIGN.md` §4.1](docs/DESIGN.md)). The `reviewgate-action`
runtime adds the GitHub REST calls described in §14.

In scope for security reports:

* Path-traversal or YAML deserialization issues in the config loader
  (`reviewgate.core.config`).
* Denial-of-service via crafted `EngineInput` JSON, large diffs, or
  pathological glob patterns.
* Token leakage or unsafe HTTP behavior in `reviewgate_action.fetch_pr`
  or `reviewgate_action.post_comment`.
* Any case where `reviewgate-core` performs an operation forbidden by
  the §4.1 boundary (network call, FS write, subprocess, etc.).
* The hosted GitHub App, webhook workers, and hosted LLM integration
  **once shipped as open-source code** from this repository or another
  repo documented in [`docs/DESIGN.md` §19](docs/DESIGN.md) under the
  same license — use the **same private reporting channels** as above;
  do not post exploit details in public issues.

Out of scope (please do not open a security report for these):

* Vulnerabilities in third-party dependencies that have an upstream
  CVE — open a regular issue or wait for our automated dependency
  updates.
* "The engine produced WARN/FAIL when I expected PASS" — that's a
  heuristic-tuning issue; please file a regular bug report.

Thank you for helping keep ReviewGate users safe.
