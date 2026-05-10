"""Public privacy page (``docs/DESIGN.md`` §5.1, §21.4, §23.1; issue #37).

Serves ``GET /privacy`` with HTML whose body text matches the design document
wording for §21.4 and the §23.1 uninstall retention privacy sentence.
"""

from __future__ import annotations

import html
from typing import Final

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

# Verbatim strings from ``docs/DESIGN.md`` (issue #37 acceptance criteria).
_DESIGN_21_4_PRIVACY_COPY: Final[str] = (
    "ReviewGate evaluates pull request metadata, changed file paths, and compact "
    "diff summaries. It does not clone repositories, execute code, or persist "
    "full repository contents by default."
)

_DESIGN_23_1_UNINSTALL_RETENTION_PRIVACY_COPY: Final[str] = (
    "If you uninstall ReviewGate, we delete analysis data associated with your "
    "installation within 30 days unless you request deletion sooner."
)

router = APIRouter(tags=["public"])


def _privacy_document_html() -> str:
    """Build the ``/privacy`` HTML document with escaped body copy."""

    section_21_4 = html.escape(_DESIGN_21_4_PRIVACY_COPY, quote=True)
    section_23_1 = html.escape(_DESIGN_23_1_UNINSTALL_RETENTION_PRIVACY_COPY, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Privacy — ReviewGate</title>
</head>
<body>
  <main>
    <h1>Privacy</h1>
    <section aria-labelledby="h-data">
      <h2 id="h-data">How we use repository data</h2>
      <p>{section_21_4}</p>
      <p><cite>Source: docs/DESIGN.md §21.4 Privacy copy</cite></p>
    </section>
    <section aria-labelledby="h-uninstall">
      <h2 id="h-uninstall">After you uninstall</h2>
      <p>{section_23_1}</p>
      <p><cite>Source: docs/DESIGN.md §23.1 Uninstall and data deletion</cite></p>
    </section>
  </main>
</body>
</html>
"""


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page() -> HTMLResponse:
    """Return the hosted privacy document."""

    return HTMLResponse(content=_privacy_document_html())
