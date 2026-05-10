"""Open-source `reviewgate-action` runtime modules.

The Python code that backs the composite step in
``reviewgate-action/action.yml``. Modules here are intentionally
stdlib-only (no third-party HTTP client) so a consumer's runner can
``pip install`` the action package on a cold cache in well under a
second; the §15 stack recommendation of ``httpx`` applies to the
hosted backend, not to the single-shot CLI invocation the Action
runs once per PR event.

Public entry points so far:

* :mod:`reviewgate_action.fetch_pr` -- fetches PR metadata and the
  paginated files list from the GitHub REST API and emits the §10.1
  ``EngineInput`` JSON document the deterministic engine consumes
  (see ``reviewgate.core.schemas.EngineInput``).
* :mod:`reviewgate_action.run_core` -- loads `.reviewgate.yml`,
  invokes :func:`reviewgate.core.engine.analyze` against a §10.1
  EngineInput, prints the §10.2 report to stdout, writes a
  Markdown summary to ``$GITHUB_STEP_SUMMARY`` when set, and
  applies the §14 ``fail-on`` policy (exit 1 at the configured
  threshold, exit 0 below it).

The Action's runtime is layered to mirror the issue plan: #24
landed the fetch step, #25 wires core invocation + ``fail-on``
policy, and #26 adds mode coexistence + comment upsert. New runtime
helpers should land as their own module rather than being grafted
onto an existing one.
"""

__all__ = ["fetch_pr", "run_core"]
