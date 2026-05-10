# ReviewGate

ReviewGate checks whether a GitHub pull request is **reviewable** before humans spend time on it.

Architecture and scope live in [`docs/DESIGN.md`](docs/DESIGN.md). The open-source deterministic engine lives under `reviewgate/core/` (see §15 in that document).

## Development

Python **3.12+** is required (see §15).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
