# Getting help with ReviewGate

ReviewGate is a developer tool that checks whether a GitHub pull request
is **reviewable** before humans spend time reviewing it. The full
product context lives in [`docs/DESIGN.md`](docs/DESIGN.md).

## Where to ask

| Question type | Best place |
| ------------- | ---------- |
| Bug, surprising verdict, crash, or regression | Open a [bug report](https://github.com/leo-aa88/reviewgate/issues/new?template=bug_report.yml). |
| Feature request, new heuristic, new threshold | Open a [feature request](https://github.com/leo-aa88/reviewgate/issues/new?template=feature_request.yml). |
| Question, design discussion, "is this in scope?" | Open a [GitHub Discussion](https://github.com/leo-aa88/reviewgate/discussions) or a discussion-style issue. |
| Security vulnerability | **Do not open a public issue.** Follow [`SECURITY.md`](SECURITY.md). |
| Hosted ReviewGate App / private beta access | Read [`docs/ONBOARDING.md`](docs/ONBOARDING.md). |

## Before you open an issue

1. Search [open](https://github.com/leo-aa88/reviewgate/issues) and
   [closed](https://github.com/leo-aa88/reviewgate/issues?q=is%3Aissue+is%3Aclosed)
   issues for the same symptom or feature.
2. Check [`docs/DESIGN.md`](docs/DESIGN.md) — most "why does the engine
   do X?" questions are answered by the §-numbered sections.
3. Read [`docs/QUICKSTART.md`](docs/QUICKSTART.md) for install,
   configuration, and a worked example.
4. If you are reporting a bug, please include:
   * a minimal reproduction (a `.json` fixture matching the §10.1
     `EngineInput` schema is ideal),
   * the verdict you saw vs. the verdict you expected,
   * the resolved `.reviewgate.yml` (or "defaults"),
   * the version of `reviewgate-core` (or commit SHA).

## Response expectations

ReviewGate is currently maintained by a small team during the private
beta. We aim to triage new issues within five business days. Pull
requests that come with a failing test are almost always merged faster
than a free-form bug report.
