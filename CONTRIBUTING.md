# Contributing to ReviewGate

Thanks for opening a PR. This document covers the rules that the CI
pipeline will check on every change. The full product context lives in
[`docs/DESIGN.md`](docs/DESIGN.md).

## reviewgate-core purity boundary

**`reviewgate.core` is a pure deterministic engine.** Per
[`docs/DESIGN.md` §4.1](docs/DESIGN.md), the engine signature is

```text
(pr_metadata, changed_files, config) -> reviewability_report
```

and the package **must not**:

- call the GitHub API,
- perform any network I/O,
- write to the filesystem,
- access a database,
- call an LLM provider,
- post comments, set labels, or create status checks,
- have any other side effect.

This boundary is load-bearing: the GitHub Action and the hosted App
([`docs/DESIGN.md` §4.2 and §4.3](docs/DESIGN.md)) are thin shells that
own all I/O around the same pure engine. Keeping the core pure is what
makes it easy to test, easy for the community to extend, and a
candidate for a Go port later.

### How CI enforces this

`tests/test_core_purity.py` parses every `.py` file under
`src/reviewgate/core/` with `ast` (no execution) and fails if any
import resolves to a forbidden module. The forbidden list is grouped by
the §4.1 rule it protects. Concrete examples (not exhaustive — see
`tests/test_core_purity.py` for the full table):

| Category (§4.1 rule) | Examples of forbidden imports |
| --- | --- |
| Third-party HTTP clients (no network) | `httpx`, `requests`, `aiohttp`, `urllib3` |
| Database drivers / clients (no database) | `sqlalchemy`, `psycopg`, `psycopg2`, `asyncpg`, `pymongo`, `redis`, `aioredis` |
| LLM provider SDKs (no LLM) | `openai`, `anthropic`, `cohere`, `mistralai`, `litellm`, `google.generativeai`, `google.genai` |
| Cloud / GitHub SDKs (no GitHub API, no side effects) | `boto3`, `botocore`, `google.cloud.*`, `github`, `githubkit`, `gidgethub` |
| Stdlib network surfaces (no network) | `socket`, `ssl`, `http.client`, `http.server`, `urllib.request`, `urllib.error`, `ftplib`, `smtplib` |
| Stdlib process spawning (no side effects) | `subprocess`, `multiprocessing`, `asyncio.subprocess` |

`urllib.parse` is **allowed** — it is pure string manipulation, not
network. The same test also asserts that `pyproject.toml` does not pull
a forbidden package into the runtime `dependencies`.

### If the purity test fails

The fix is almost always to **move the offending dependency to the
GitHub Action layer (`src/reviewgate_action/`) or the hosted App
service layer** (open-source roadmap; see `docs/DESIGN.md` §19),
where I/O is allowed. Expanding the allow-list
should be the last resort and requires updating the §4.1 contract first.

If you genuinely need a stdlib network module for an *internal* purpose
that has no I/O semantics in your code (very rare), open an issue
referencing §4.1 before relaxing the contract.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Python **3.12+** is required (see `docs/DESIGN.md` §15).

## Pull requests

- Strong typing everywhere; `Any` requires an explicit justification.
- Google-style docstrings on public modules, classes, and functions.
- Non-test source files: prefer **< 500 LOC**, hard cap **600 LOC**.
- No backwards-compatibility shims unless explicitly agreed.
- No unexplained magic numbers or magic strings — use named constants.
- No race conditions; KISS, DRY, clean code, SOLID where it helps.
