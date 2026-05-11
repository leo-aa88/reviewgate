"""Derive analysis keys for §13.7 enqueue dedupe before Dramatiq ``send`` (issue #47).

Mechanism **#2** in ``docs/DESIGN.md`` §13.7: before ``Actor.send``, compute the same
five-part composite key the worker uses and skip enqueue when a ``completed``
``analyses`` row already exists. Fail-open on missing credentials, unknown
repositories, or GitHub access errors so webhooks still enqueue conservatively.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx
from sqlalchemy import select

from reviewgate.app.analysis.config_hash import fetch_reviewgate_yml_and_config_hash
from reviewgate.app.analysis.pr_metadata_hash import compute_pr_metadata_hash
from reviewgate.app.github.auth import GitHubAppAuthError, fetch_installation_access_token
from reviewgate.app.github.client import GitHubRestError
from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.models import Repository
from reviewgate.app.storage.repositories import (
    AnalysisNaturalKey,
    completed_analysis_exists_for_key,
)

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT: Final[float] = 30.0


def evaluate_pull_request_enqueue_dedupe(
    settings: AppSettings,
    payload: dict[str, Any],
) -> tuple[bool, dict[str, object]]:
    """Return ``(skip_enqueue, reviewgate_envelope_fields)`` per §13.7 item #2.

    When ``skip_enqueue`` is ``True``, the HTTP handler should return **202**
    without queueing. ``reviewgate_envelope_fields`` is non-empty when the
    composite key was derived successfully and enqueue proceeds, so the worker
    can align with the same ``repository_id`` / hashes / head SHA tuple.
    """

    try:
        return _evaluate_pull_request_enqueue_dedupe_inner(settings, payload)
    except (GitHubAppAuthError, GitHubRestError, httpx.HTTPError, OSError) as exc:
        logger.info(
            "enqueue dedupe: GitHub access failed; continuing with enqueue (%s)",
            exc,
        )
        return False, {}
    except (TypeError, ValueError) as exc:
        logger.info(
            "enqueue dedupe: unexpected payload shape; continuing with enqueue (%s)",
            exc,
        )
        return False, {}


def _evaluate_pull_request_enqueue_dedupe_inner(
    settings: AppSettings,
    payload: dict[str, Any],
) -> tuple[bool, dict[str, object]]:
    inst_repo = _parse_installation_repository_ids(payload)
    if inst_repo is None:
        return False, {}
    github_installation_id, github_repository_id = inst_repo

    if settings.github_app_id is None or settings.github_app_private_key is None:
        return False, {}

    pr_obj = payload.get("pull_request")
    if not isinstance(pr_obj, dict):
        return False, {}

    raw_number = pr_obj.get("number")
    if isinstance(raw_number, bool) or not isinstance(raw_number, int) or raw_number < 1:
        return False, {}

    head_obj = pr_obj.get("head")
    base_obj = pr_obj.get("base")
    if not isinstance(head_obj, dict) or not isinstance(base_obj, dict):
        return False, {}

    head_sha_raw = head_obj.get("sha")
    base_ref_raw = base_obj.get("ref")
    if not isinstance(head_sha_raw, str) or not head_sha_raw.strip():
        return False, {}
    if not isinstance(base_ref_raw, str) or not base_ref_raw.strip():
        return False, {}

    repo_obj = payload.get("repository")
    if not isinstance(repo_obj, dict):
        return False, {}
    owner_obj = repo_obj.get("owner")
    if not isinstance(owner_obj, dict):
        return False, {}
    owner_login = owner_obj.get("login")
    short_name = repo_obj.get("name")
    if not isinstance(owner_login, str) or not owner_login.strip():
        return False, {}
    if not isinstance(short_name, str) or not short_name.strip():
        return False, {}

    engine = create_engine_from_settings(settings)
    if engine is None:
        return False, {}

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        repository_uuid = session.scalar(
            select(Repository.id).where(
                Repository.github_repository_id == github_repository_id,
            ),
        )
        if repository_uuid is None:
            return False, {}

    raw_title = pr_obj.get("title")
    pr_title = raw_title if isinstance(raw_title, str) else None
    raw_body = pr_obj.get("body")
    pr_body = raw_body if isinstance(raw_body, str) else None

    meta_hash = compute_pr_metadata_hash(
        title=pr_title,
        body=pr_body,
        base_branch=base_ref_raw.strip(),
    )

    with httpx.Client(timeout=_HTTP_TIMEOUT) as http_client:
        access = fetch_installation_access_token(
            settings,
            github_installation_id,
            http_client=http_client,
        )
        config_hash, _cfg = fetch_reviewgate_yml_and_config_hash(
            access.token,
            owner=owner_login.strip(),
            repo=short_name.strip(),
            base_ref=base_ref_raw.strip(),
            http_client=http_client,
        )

    key = AnalysisNaturalKey(
        repository_id=repository_uuid,
        pull_number=raw_number,
        head_sha=head_sha_raw.strip(),
        config_hash=config_hash,
        pr_metadata_hash=meta_hash,
    )

    with session_factory() as session:
        if completed_analysis_exists_for_key(session, key):
            return True, {}

    fields: dict[str, object] = {
        "reviewgate_repository_id": str(key.repository_id),
        "reviewgate_pull_number": key.pull_number,
        "reviewgate_head_sha": key.head_sha,
        "reviewgate_config_hash": key.config_hash,
        "reviewgate_pr_metadata_hash": key.pr_metadata_hash,
    }
    return False, fields


def _parse_installation_repository_ids(
    payload: dict[str, Any],
) -> tuple[int, int] | None:
    """Return GitHub ``installation.id`` and ``repository.id`` when both exist."""

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
