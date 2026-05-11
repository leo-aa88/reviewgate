"""Console entrypoint for ``reviewgate-worker`` (Dramatiq process, issue #30).

Thin wrapper around ``python -m dramatiq`` so operators do not need to
remember the bootstrap module path. Extra CLI arguments are forwarded unchanged
to Dramatiq (for example ``--processes`` or ``--threads``).
"""

from __future__ import annotations

import importlib.util
import sys
from subprocess import call

_APP_EXTRA_MESSAGE = (
    "reviewgate-worker requires the hosted app dependencies. "
    'Install them with: pip install "reviewgate[app]"'
)


def main() -> None:
    """Run Dramatiq targeting :mod:`reviewgate.app.analysis.worker_app`."""

    if importlib.util.find_spec("dramatiq") is None:
        raise SystemExit(_APP_EXTRA_MESSAGE)

    argv = [
        sys.executable,
        "-m",
        "dramatiq",
        "reviewgate.app.analysis.worker_app",
        *sys.argv[1:],
    ]
    raise SystemExit(call(argv))
