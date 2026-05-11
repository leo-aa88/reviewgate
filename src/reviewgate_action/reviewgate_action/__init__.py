"""Open-source `reviewgate-action` runtime modules.

The Python code that backs the composite steps in the root ``action.yml``
and legacy ``src/reviewgate_action/action.yml``. Modules here are intentionally
stdlib-only (no third-party HTTP client) so a consumer's runner can
``pip install`` the action package on a cold cache in well under a
second; the §15 stack recommendation of ``httpx`` applies to the
hosted backend, not to the single-shot CLI invocation the Action
runs once per PR event.

Public entry points:

* :mod:`reviewgate_action.fetch_pr` -- fetches PR metadata and the
  paginated files list from the GitHub REST API and emits the §10.1
  ``EngineInput`` JSON document the deterministic engine consumes
  (see ``reviewgate.core.schemas.EngineInput``).
* :mod:`reviewgate_action.run_core` -- loads `.reviewgate.yml`,
  invokes :func:`reviewgate.core.engine.analyze` against a §10.1
  EngineInput, prints the §10.2 report to stdout, writes a
  Markdown summary to ``$GITHUB_STEP_SUMMARY`` when set, applies
  the §14 ``fail-on`` policy, and (when §14.1 coexistence allows)
  delegates to :mod:`reviewgate_action.post_comment` to upsert the
  §13 PR comment.
* :mod:`reviewgate_action.coexistence` -- pure resolver for the
  §14.1 coexistence table (`mode: auto` / `action` / `quiet` x
  `.reviewgate.yml` `mode: app` / `action` / `both`).
* :mod:`reviewgate_action.post_comment` -- §13 PR-comment upsert
  against the GitHub Issues API. Uses an HTML-comment marker
  embedded in the body so the upsert can re-find the bot's comment
  after a force-push or workflow re-run.

The runtime landed in three issues: #24 added :mod:`fetch_pr`,
#25 added :mod:`run_core` + the §14 ``fail-on`` policy, and #26
added :mod:`coexistence` + :mod:`post_comment` so the Action and
the hosted App stay in their lanes.
"""

__all__ = ["coexistence", "fetch_pr", "post_comment", "run_core"]
