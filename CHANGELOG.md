# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Until the public `1.0.0` release, the public API surface (the
`reviewgate.core` package and the `reviewgate-action` action contract)
should be considered stable but subject to additive change.

## [Unreleased]

### Changed

- **PR follow-up (stable API + guards):** ``PrAuthorKind``, automation login
  frozensets, and ``finalize_size_stats_for_pr_author`` are re-exported from
  ``reviewgate.core``; ``engine.analyze`` asserts disjoint keys when merging
  ``SizeStats`` with automation extras; Markdown stats rendering moved to
  ``reviewgate_action.summary`` (``run_core`` under the soft LOC cap).
- **§10.4.1 / §10.4.2 PR opener metadata:** §10.2 ``stats`` always includes
  ``pr_author_kind`` (``human`` | ``dependency_automation`` |
  ``coding_agent_automation`` | ``generic_automation``) and
  ``pr_author_login`` when non-empty, classifying GitHub ``user.login``
  only—dependency bots (Dependabot / Renovate), coding-agent integration
  accounts (shipped allow-list: Copilot, Cursor, Codex connector, Claude
  App, Devin integration, …), other ``[bot]`` identities, or human
  collaborators (`docs/DESIGN.md` §10.4.2). Known dependency bots still get
  the §10.4.1 manifest-only ``human_loc_changed`` clamp when the diff has
  no ``source`` files. Action summaries print the author class and keep the
  §10.4 ``human_loc_changed`` label.

### Added

- **Excessive code-comment verbosity heuristic (issue #143):** new
  [`code_comments.py`](src/reviewgate/core/code_comments.py) core module
  emits deterministic `oversized_comment_block`, `excessive_comment_lines`,
  and `comment_heavy_diff` warnings from the added lines of
  `ChangedFile.patch` only. Eligibility reuses the categorizer's
  `source` + `human_authored` verdict; a conservative lexical scanner
  recognizes full-line `#` / `//` / `/* */` comments in Python, Shell,
  JavaScript, TypeScript (not JSX/TSX), and Go while never counting
  string literals (including Python docstrings) or trailing comments;
  the one documented exception is a hunk that begins inside a docstring
  opened above Git's context window (see README).
  Java, C/C++, C#, Rust, and JSX/TSX are skipped until their multiline
  string forms are modeled. Configured via the new `policy.code_comments`
  block (warn/fail thresholds, ratio sample-size guard, `enabled` toggle);
  adds `comment_lines_added`, `code_lines_added`,
  `largest_comment_block_lines`, and `comment_ratio` stats when enabled,
  and maps no new labels (existing §10.13 aggregation is unchanged).
  Follow-up to the #144 review: unchanged context lines participate in
  lexical classification without being tallied; JS/Go backtick strings
  and shell quotes/heredocs carry across lines;
  `oversized_comment_block` is one PR-level warning for the maximum
  block (filename in evidence), matching `size_warnings`;
  `files` / `file_categories` pairs are rejected when filenames differ;
  unified-diff file headers are detected by position rather than by
  content; unclassified source languages join the `comment_ratio`
  denominator instead of vanishing from it; mid-file hunks are analyzed
  once their own context establishes a normal code position, and that
  establishment requires a code token on each evidential context line
  rather than merely a clean scan.
  Hunks that do not start at new-file line 0 or 1 are no longer skipped
  outright: they are analyzed once their own context lines establish a
  normal code position (two consecutive context lines that all scan
  clean), so an ordinary mid-file edit -- the case issue #143 describes
  -- is measured instead of ignored. Unified-diff file headers are now
  detected by position (everything before the first `@@` is preamble)
  rather than by a `+++ ` / `--- ` content match, which closes collisions
  with an added `++i` and a deleted shell `-- )`. An in-scope source file
  whose language is not modeled now contributes its added non-blank
  lines to the `comment_ratio` denominator (never the numerator), so an
  unparsed language can no longer inflate the ratio. The scanner moved
  to [`_comment_lex.py`](src/reviewgate/core/_comment_lex.py) and
  [`_comment_scan.py`](src/reviewgate/core/_comment_scan.py), and the
  `CodeComment*` policy models to
  [`comment_policy.py`](src/reviewgate/core/comment_policy.py), keeping
  every non-test source file under the CONTRIBUTING.md LOC preference.
- **OSS polish (issue #126):** [`GOVERNANCE.md`](GOVERNANCE.md); canonical
  hosted-stack local guide [`docs/HOSTED_LOCAL.md`](docs/HOSTED_LOCAL.md) with
  README cross-links (including Dependabot, already configured in
  [`.github/dependabot.yml`](.github/dependabot.yml)).
- **DESIGN.md §12 / issue #124 alignment:** `ignored_paths` filtering in the
  deterministic engine; warn-tier counts for risky / dependency / config file
  volumes; `missing_tests_for_source` heuristic (skipped for very large diffs
  and non-human-authored “source” rows); `Labels` entries for `needs-tests`,
  `dependency-change`, and `config-change`; `status_check.fail_on` mapping in
  GitHub check conclusions; `purge_analyses_for_old_uninstalls` Dramatiq actor
  plus `purge_analyses_for_uninstalled_installations` storage helper for the
  §23.1 retention window.

### Changed

- **Default `thresholds.warn.risky_files_changed`** is now **2** (documented in
  `docs/DESIGN.md` §10.3) so single-file risky edits rely on the rationale
  heuristic without a redundant count warning; set `1` in `.reviewgate.yml` to
  restore the stricter table.
- **Hosted pipeline:** `.reviewgate.yml` is fetched before the §22.3 fail-fast
  short-circuit so `policy.fail_on_huge_pr` can soften the >1000-files tier.
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
