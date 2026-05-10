# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Until the public `1.0.0` release, the public API surface (the
`reviewgate.core` package and the `reviewgate-action` action contract)
should be considered stable but subject to additive change.

## [Unreleased]

### Changed

- Public open-source release under Apache License 2.0.
- Comprehensive README, contributor docs, issue/PR templates, and a
  private-beta `docs/QUICKSTART.md` walkthrough.

## [0.1.0] - 2026-05-10

First public release of `reviewgate-core` (the deterministic
reviewability engine) and `reviewgate-action` (the GitHub Action
wrapper). All work is tracked against
[`docs/DESIGN.md`](docs/DESIGN.md).

### Added

- **`reviewgate-core` deterministic engine** (DESIGN.md §4.1, §10).
  - Strict Pydantic schemas for the §10.1 `EngineInput` envelope and
    the §10.2 `ReviewabilityReport` output.
  - Per-file categorization with the closed §10.5 category set
    (source / test / docs / config / dependency / lockfile / migration
    / infra / auth / billing / generated / snapshot / vendored /
    minified / asset / unknown).
  - §10.3 size warnings on raw and human-authored LOC, with §10.4
    exclusion of generated, lockfile, snapshot, vendored, and minified
    LOC from the human-authored total.
  - §10.6–§10.9 default path patterns for risky paths, dependency
    files, lockfiles, generated/vendored/minified/snapshot files, and
    test files.
  - §10.10 weak-PR-body, missing-linked-issue, and risky-paths-without-
    rationale heuristics.
  - §10.11 mixed-concern detection on suspicious category clusters.
  - §10.13 baseline `PASS` / `WARN` / `FAIL` aggregation from warning
    severities.
  - §13.9 + §12 suggested-label assembly from warnings and the
    user-configurable label map.
  - `reviewgate-core` CLI (§5.1, §25 M1) for fixture-driven runs over
    JSON files or stdin.
- **`.reviewgate.yml` configuration** (DESIGN.md §12).
  - Strict YAML schema with malformed-config recovery: bad config
    never crashes analysis; the engine attaches a `config_invalid`
    warning and runs on defaults.
  - Per-repo overrides for thresholds, risky paths, ignored paths,
    label names, status-check name, `mode`, and `llm_reports`.
- **`reviewgate-action` GitHub Action** (DESIGN.md §14).
  - `action.yml` with the documented inputs (`github-token`,
    `fail-on`, `post-comment`, `mode`, `python-version`,
    `working-directory`) and outputs (`reviewability`, `report-json`).
  - `reviewgate_action.fetch_pr`: PR metadata + paginated changed-files
    fetch from the GitHub REST API into a §10.1 `EngineInput` JSON
    document.
  - `reviewgate_action.run_core`: loads `.reviewgate.yml`, invokes the
    deterministic engine, prints the §10.2 report, writes a Markdown
    summary to `$GITHUB_STEP_SUMMARY`, applies the §14 `fail-on`
    policy.
  - `reviewgate_action.coexistence`: pure resolver for the §14.1
    coexistence table.
  - `reviewgate_action.post_comment`: §13 PR-comment upsert with the
    hidden marker, with an HTML-comment marker so the upsert finds the
    bot's previous comment after force-pushes and re-runs.
- **§4.1 purity boundary enforcement.**
  `tests/test_core_purity.py` parses every `.py` file under
  `src/reviewgate/core/` with `ast` and fails CI on any forbidden
  import (HTTP clients, GitHub SDKs, LLM SDKs, DB drivers, stdlib
  network, `subprocess`, etc.). The same test asserts that
  `pyproject.toml` runtime dependencies do not pull in forbidden
  packages.
- **§24.2 golden fixtures.** Fourteen fixtures covering every PR
  shape from the design document (small clean PR, large human PR,
  large lockfile-only, generated-code PR, snapshot-heavy, risky
  migration, auth without context, dependency-only, dependency +
  behavior, normal source+tests+docs feature, suspicious billing+auth+
  infra, massive refactor, docs-only, test-only).
- **Documentation.**
  - [`docs/DESIGN.md`](docs/DESIGN.md): full product design.
  - [`docs/ONBOARDING.md`](docs/ONBOARDING.md): private-beta
    onboarding walkthrough.
  - [`docs/QUICKSTART.md`](docs/QUICKSTART.md): five-minute installer
    + `.reviewgate.yml` tutorial for new contributors.

[Unreleased]: https://github.com/leo-aa88/reviewgate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/leo-aa88/reviewgate/releases/tag/v0.1.0
