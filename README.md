# reviewgate-core

This repository **is** **reviewgate-core**: the open-source, deterministic reviewability engine for ReviewGate, which checks whether a pull request is **reviewable** before humans spend time on it.

Product context, boundaries, and the full stack live in [`docs/DESIGN.md`](docs/DESIGN.md). The Python implementation of the engine is under `reviewgate/core/` (§15). The GitHub Action and hosted app are separate codebases described in that document.

## Development

Python **3.12+** is required (see §15).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
