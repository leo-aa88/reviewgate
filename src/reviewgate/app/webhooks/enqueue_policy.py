"""Rules for whether a ``pull_request`` webhook may enqueue work (issue #36).

``docs/DESIGN.md`` §23.1: after uninstall, ignore future processing for that
installation. The HTTP handler consults :func:`pull_request_may_enqueue` after
dedupe claims; :func:`installation_repository_may_enqueue_jobs` is reused by
the worker stub as a second line of defense for messages already in Redis.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.models import Installation, Repository


def installation_repository_may_enqueue_jobs(
    session: Session,
    *,
    github_installation_id: int,
    github_repository_id: int,
) -> bool:
    """Return ``False`` when the hosted row says this repo must not run jobs.

    If no ``repositories`` row exists yet for ``github_repository_id``, returns
    ``True`` so first-time installs stay enqueueable until ``installation``
    webhooks populate metadata.

    If a row exists for the repository but its parent ``installations`` row
    does not match ``github_installation_id`` (reassignment edge case), returns
    ``True`` and defers to future reconciliation logic.
    """

    row = session.execute(
        select(Repository, Installation)
        .join(Installation, Installation.id == Repository.installation_id)
        .where(Repository.github_repository_id == github_repository_id),
    ).one_or_none()
    if row is None:
        return True
    repo, inst = row
    if inst.github_installation_id != github_installation_id:
        return True
    return inst.deleted_at is None and repo.active


def _parse_pr_installation_repository_ids(
    payload: dict[str, Any],
) -> tuple[int, int] | None:
    inst_block = payload.get("installation")
    repo_block = payload.get("repository")
    if not isinstance(inst_block, dict) or not isinstance(repo_block, dict):
        return None
    raw_i = inst_block.get("id")
    raw_r = repo_block.get("id")
    if isinstance(raw_i, bool) or not isinstance(raw_i, int):
        return None
    if isinstance(raw_r, bool) or not isinstance(raw_r, int):
        return None
    return raw_i, raw_r


def pull_request_may_enqueue(settings: AppSettings, payload: dict[str, Any]) -> bool:
    """Return whether a ``pull_request`` delivery should enqueue analysis work."""

    parsed = _parse_pr_installation_repository_ids(payload)
    if parsed is None:
        return True

    github_installation_id, github_repository_id = parsed
    engine = create_engine_from_settings(settings)
    if engine is None:
        return True

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        return installation_repository_may_enqueue_jobs(
            session,
            github_installation_id=github_installation_id,
            github_repository_id=github_repository_id,
        )
