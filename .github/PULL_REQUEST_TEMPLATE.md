<!--
Thanks for sending a PR to ReviewGate.

Before opening, please:
- Read CONTRIBUTING.md (especially the §4.1 reviewgate-core purity boundary).
- Make sure the change is anchored to a section of docs/DESIGN.md or
  links to an existing issue.
- Run `pytest` locally with Python 3.12+.
-->

## What and why

<!-- One short paragraph: what does this change do, and why now?
     Tie back to a docs/DESIGN.md §-numbered section or a tracked issue. -->

Closes <!-- #issue -->.

## Design link

<!-- Which DESIGN.md section drives this change? e.g. §10.6 risky paths,
     §13 PR comment, §14.1 coexistence, §12 .reviewgate.yml, etc. -->

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature / heuristic (non-breaking change that adds capability)
- [ ] Breaking change (verdict change, schema change, public-API rename)
- [ ] Documentation only
- [ ] Tests / CI / build only

## Checklist

- [ ] My change keeps `reviewgate.core` pure (no network, no FS write,
      no DB, no LLM, no GitHub API; see `CONTRIBUTING.md`).
- [ ] If I added a new heuristic, I added a deterministic warning code,
      a severity, and at least one fixture under
      `tests/fixtures/m2_golden/` (or a unit test).
- [ ] If I changed the §10.2 report or §10.1 input schema, I bumped the
      relevant version markers and updated `docs/DESIGN.md` and/or
      `CHANGELOG.md`.
- [ ] If I changed `.reviewgate.yml` (§12), I updated the schema, the
      malformed-config recovery test, and `docs/QUICKSTART.md` /
      `docs/ONBOARDING.md` examples.
- [ ] I ran `pytest` and the suite passes on Python 3.12+.
- [ ] I added or updated Google-style docstrings on any new public API.
- [ ] I kept files under the soft cap (~500 LOC; hard cap 600) per
      `CONTRIBUTING.md`.

## Verdict / behavior diff (optional but encouraged)

<!--
If this PR can change a verdict on existing repos, paste a short
before/after for at least one fixture under tests/fixtures/m2_golden/.
This is the single most useful thing for reviewers.

Example:

| Fixture | Before | After |
| ------- | ------ | ----- |
| 06_risky_migration_pr.json | WARN | FAIL |
-->

## Notes for reviewers

<!-- Anything reviewers should look at first; alternatives you considered. -->
