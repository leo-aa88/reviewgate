"""Persist GitHub App ``installation`` webhooks (``docs/DESIGN.md`` §13.2, §16.1).

Handles ``installation`` ``created`` / ``deleted`` plus ``installation_repositories``
``added`` / ``removed`` (issues #35, #36).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.models import Installation, Repository


def persist_installation_webhook_payload(
    settings: AppSettings,
    *,
    event_name: str,
    action: str,
    payload: dict[str, Any],
) -> None:
    """Persist ``installations`` / ``repositories`` for supported webhook actions.

    Args:
        settings: Application settings (requires ``REVIEWGATE_DATABASE_URL``).
        event_name: ``X-GitHub-Event`` value (``installation`` or
            ``installation_repositories``).
        action: Payload ``action`` field (for example ``created``, ``deleted``,
            ``added``, ``removed``).
        payload: Parsed JSON object from the webhook body.

    Raises:
        RuntimeError: If no database URL / engine can be built.
        ValueError: If required payload fields are missing or malformed.

    ``OperationalError`` from the driver propagates to the HTTP layer for a
    **503** response.
    """

    engine = create_engine_from_settings(settings)
    if engine is None:
        msg = "persist_installation_webhook_payload requires REVIEWGATE_DATABASE_URL"
        raise RuntimeError(msg)

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        if event_name == "installation":
            if action == "created":
                _installation_created(session, payload)
            elif action == "deleted":
                _installation_deleted(session, payload)
        elif event_name == "installation_repositories":
            if action == "added":
                _installation_repositories_added(session, payload)
            elif action == "removed":
                _installation_repositories_removed(session, payload)
        session.commit()


def _installation_block(payload: dict[str, Any]) -> dict[str, Any]:
    inst = payload.get("installation")
    if not isinstance(inst, dict):
        msg = "webhook payload is missing installation"
        raise ValueError(msg)
    return inst


def _parse_github_installation_id(inst: dict[str, Any]) -> int:
    raw = inst.get("id")
    if isinstance(raw, bool) or not isinstance(raw, int):
        msg = "installation.id must be an integer"
        raise ValueError(msg)
    return raw


def _parse_account(inst: dict[str, Any]) -> tuple[str, str]:
    account = inst.get("account")
    if not isinstance(account, dict):
        msg = "installation.account must be an object"
        raise ValueError(msg)
    login = account.get("login")
    account_type = account.get("type")
    if not isinstance(login, str) or not login.strip():
        msg = "installation.account.login must be a non-empty string"
        raise ValueError(msg)
    if not isinstance(account_type, str) or not account_type.strip():
        msg = "installation.account.type must be a non-empty string"
        raise ValueError(msg)
    return login.strip(), account_type.strip()


def _upsert_installation_row(
    session: Session,
    *,
    github_installation_id: int,
    account_login: str,
    account_type: str,
) -> uuid.UUID:
    stmt = (
        pg_insert(Installation)
        .values(
            github_installation_id=github_installation_id,
            account_login=account_login,
            account_type=account_type,
            deleted_at=None,
        )
        .on_conflict_do_update(
            index_elements=["github_installation_id"],
            set_={
                "account_login": account_login,
                "account_type": account_type,
                "deleted_at": None,
            },
        )
        .returning(Installation.id)
    )
    row_id = session.execute(stmt).scalar_one()
    return row_id


def _upsert_repository_row(
    session: Session,
    *,
    installation_uuid: uuid.UUID,
    github_repository_id: int,
    owner: str,
    name: str,
    full_name: str,
    private: bool,
    active: bool,
) -> None:
    stmt = (
        pg_insert(Repository)
        .values(
            installation_id=installation_uuid,
            github_repository_id=github_repository_id,
            owner=owner,
            name=name,
            full_name=full_name,
            private=private,
            active=active,
        )
        .on_conflict_do_update(
            index_elements=["github_repository_id"],
            set_={
                "installation_id": installation_uuid,
                "owner": owner,
                "name": name,
                "full_name": full_name,
                "private": private,
                "active": active,
            },
        )
    )
    session.execute(stmt)


def _parse_repository_dict(repo: dict[str, Any]) -> tuple[int, str, str, str, bool]:
    raw_id = repo.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        msg = "repository id must be an integer"
        raise ValueError(msg)
    name = repo.get("name")
    full_name = repo.get("full_name")
    if not isinstance(name, str) or not name.strip():
        msg = "repository.name must be a non-empty string"
        raise ValueError(msg)
    owner_login = ""
    owner = repo.get("owner")
    if isinstance(owner, dict):
        raw_login = owner.get("login")
        if isinstance(raw_login, str):
            owner_login = raw_login.strip()
    if not owner_login:
        msg = "repository.owner.login is required"
        raise ValueError(msg)
    if isinstance(full_name, str) and full_name.strip():
        fn = full_name.strip()
    else:
        fn = f"{owner_login}/{name.strip()}"
    private_val = repo.get("private")
    private = bool(private_val) if isinstance(private_val, bool) else False
    return raw_id, owner_login, name.strip(), fn, private


def _installation_deleted(session: Session, payload: dict[str, Any]) -> None:
    """Soft-delete the installation and deactivate all linked repositories."""

    inst = _installation_block(payload)
    gid = _parse_github_installation_id(inst)
    installation_uuid = session.execute(
        select(Installation.id).where(Installation.github_installation_id == gid),
    ).scalar_one_or_none()
    if installation_uuid is None:
        return

    now = datetime.now(timezone.utc)
    session.execute(
        update(Installation)
        .where(
            Installation.id == installation_uuid,
            Installation.deleted_at.is_(None),
        )
        .values(deleted_at=now),
    )
    session.execute(
        update(Repository)
        .where(Repository.installation_id == installation_uuid)
        .values(active=False),
    )


def _installation_created(session: Session, payload: dict[str, Any]) -> None:
    inst = _installation_block(payload)
    gid = _parse_github_installation_id(inst)
    account_login, account_type = _parse_account(inst)
    installation_uuid = _upsert_installation_row(
        session,
        github_installation_id=gid,
        account_login=account_login,
        account_type=account_type,
    )

    repos = payload.get("repositories")
    if repos is None:
        return
    if not isinstance(repos, list):
        msg = "repositories must be an array when present"
        raise ValueError(msg)

    for item in repos:
        if not isinstance(item, dict):
            continue
        try:
            rid, owner, short_name, fn, private = _parse_repository_dict(item)
        except ValueError:
            continue
        _upsert_repository_row(
            session,
            installation_uuid=installation_uuid,
            github_repository_id=rid,
            owner=owner,
            name=short_name,
            full_name=fn,
            private=private,
            active=True,
        )


def _installation_repositories_added(session: Session, payload: dict[str, Any]) -> None:
    inst = _installation_block(payload)
    gid = _parse_github_installation_id(inst)
    try:
        account_login, account_type = _parse_account(inst)
    except ValueError as exc:
        existing = session.execute(
            select(Installation).where(Installation.github_installation_id == gid),
        ).scalar_one_or_none()
        if existing is None:
            msg = "installation.account is required when the installation is unknown"
            raise ValueError(msg) from exc
        account_login = existing.account_login
        account_type = existing.account_type
    installation_uuid = _upsert_installation_row(
        session,
        github_installation_id=gid,
        account_login=account_login,
        account_type=account_type,
    )

    added = payload.get("repositories_added")
    if added is None:
        return
    if not isinstance(added, list):
        msg = "repositories_added must be an array when present"
        raise ValueError(msg)

    for item in added:
        if not isinstance(item, dict):
            msg = "repositories_added entries must be objects"
            raise ValueError(msg)
        rid, owner, short_name, fn, private = _parse_repository_dict(item)
        _upsert_repository_row(
            session,
            installation_uuid=installation_uuid,
            github_repository_id=rid,
            owner=owner,
            name=short_name,
            full_name=fn,
            private=private,
            active=True,
        )


def _installation_repositories_removed(session: Session, payload: dict[str, Any]) -> None:
    inst = _installation_block(payload)
    gid = _parse_github_installation_id(inst)
    installation_uuid = _resolve_installation_uuid(session, github_installation_id=gid)

    removed = payload.get("repositories_removed")
    if removed is None:
        return
    if not isinstance(removed, list):
        msg = "repositories_removed must be an array when present"
        raise ValueError(msg)

    repo_ids: list[int] = []
    for item in removed:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            continue
        repo_ids.append(raw_id)

    if not repo_ids:
        return

    session.execute(
        update(Repository)
        .where(
            Repository.installation_id == installation_uuid,
            Repository.github_repository_id.in_(repo_ids),
        )
        .values(active=False),
    )


def _resolve_installation_uuid(session: Session, *, github_installation_id: int) -> uuid.UUID:
    stmt = select(Installation.id).where(
        Installation.github_installation_id == github_installation_id,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        msg = "installation is not registered; expected installation.created first"
        raise ValueError(msg)
    return row
