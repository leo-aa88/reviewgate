# Governance

This document describes how the ReviewGate open-source project is run
today. It is **informal**: there is no elected steering committee or
formal voting process. The goal is predictable, low-friction
collaboration aligned with [`docs/DESIGN.md`](docs/DESIGN.md).

## Maintainer

The repository is maintained by the owner listed in
[`pyproject.toml`](pyproject.toml) (`authors`) and in Git history.
Day-to-day triage, releases, and merge decisions for `main` rest with
the maintainer unless delegated in a specific issue or PR thread.

## How decisions are made

* **Product and architecture:** [`docs/DESIGN.md`](docs/DESIGN.md) is
  the source of truth for scope, invariants, and the §10 engine
  contract. Substantive changes should reference a §-numbered section
  or an open issue.
* **Contributions:** Pull requests are reviewed for correctness,
  tests, and alignment with [`CONTRIBUTING.md`](CONTRIBUTING.md)
  (including the §4.1 purity boundary for `reviewgate-core`).
* **Disagreement:** Prefer discussion on the issue or PR. If a change
  is blocked, the maintainer decides whether to merge, defer, or close
  with rationale.

## Releases

Release cadence and versioning follow [Semantic Versioning](https://semver.org)
and [Keep a Changelog](https://keepachangelog.com). Release artifacts
(PyPI, GitHub Releases) are published when the maintainer tags a
release; until the first public release tag, treat the public API as
stable but additive (see [README § Status](README.md#status)).

## Security

Do not use this file for vulnerability reports. Follow
[`SECURITY.md`](SECURITY.md) and GitHub private vulnerability reporting.

## Code of conduct

All participants are expected to follow
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
