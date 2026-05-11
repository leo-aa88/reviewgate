# Manual private-beta QA checklist

This checklist mirrors `docs/DESIGN.md` §24.4. Run it **before each beta
install** on a test repository where the ReviewGate GitHub App is authorized.

Print this file or keep it open while you execute the steps; record pass/fail
and any anomalies in your team’s QA log.

## Checklist (§24.4)

- [ ] Install the GitHub App on a **test repo** (see `docs/ONBOARDING.md`).
- [ ] Open a **small** pull request (few files, modest diff); confirm an
      analysis runs and the PR receives feedback.
- [ ] Open a **large** pull request (or use a branch that exceeds warn/fail
      thresholds); confirm behaviour matches policy (comment, labels, status).
- [ ] **Edit the PR body** (and optionally title); confirm a new or updated
      analysis reflects the change where expected.
- [ ] **Push an update** to the PR branch; confirm synchronize handling
      (comment/check refresh, no stuck states).
- [ ] Confirm the **PR comment** updates appropriately (ReviewGate marker,
      concise body).
- [ ] Confirm **labels** update to match the current reviewability suggestion
      set.
- [ ] Confirm the **status check** conclusion matches the final reviewability
      and your `status_check` / `fail_on` configuration.
- [ ] Confirm **no duplicate** ReviewGate comments (single upsert target).

## Related

- Hosted feedback form: `GET /feedback` on the deployment (issue #55).
- Privacy copy: `GET /privacy` (issue #37).
- Onboarding walkthrough: `docs/ONBOARDING.md` (issue #27).
