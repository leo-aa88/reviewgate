"""GitHub webhook delivery dedupe using ``webhook_deliveries`` (``docs/DESIGN.md`` §13.3, §16.1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Final, Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError

from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.models import WebhookDelivery

ClaimResult = Literal["claimed", "duplicate", "active", "database_unavailable"]

#: Maximum duration in seconds a delivery claim/lease is held before an in-flight
#: or crashed attempt is considered expired and can be reclaimed by a retry.
_DEFAULT_LEASE_TIMEOUT_SECONDS: Final[int] = 180


def claim_github_webhook_delivery(
    settings: AppSettings,
    *,
    delivery_id: str,
    event_name: str,
    lease_timeout_seconds: int | None = None,
) -> tuple[ClaimResult, uuid.UUID | None]:
    """Atomically claim a delivery id using PostgreSQL upsert with lease semantics.

    Uses ``INSERT ... ON CONFLICT (github_delivery_id) DO UPDATE ... WHERE processed IS false AND claimed_at < :cutoff RETURNING id``
    so that:
    1. New deliveries are inserted with ``processed=False``, ``claimed_at=now``, and a fresh ``claim_token``.
    2. Previously failed or crashed attempts (where ``processed=False`` and the lease
       expired or was released) are atomically updated with a fresh lease and new ``claim_token``.
    3. Active in-progress deliveries (where ``processed=False`` and the lease is still valid)
       return ``("active", None)`` so the HTTP layer surfaces a retryable **503** response.
    4. Processed deliveries (``processed=True``) return ``("duplicate", None)`` acknowledging **202**.

    Args:
        settings: Application settings (``REVIEWGATE_DATABASE_URL``).
        delivery_id: ``X-GitHub-Delivery`` header value.
        event_name: ``X-GitHub-Event`` header value.
        lease_timeout_seconds: Lease timeout window in seconds (defaults to settings.webhook_delivery_lease_seconds).

    Returns:
        A tuple ``(result, claim_token)`` where ``result`` is one of:
        - ``"claimed"``: new or stale delivery claimed; ``claim_token`` is returned.
        - ``"duplicate"``: already processed; ``claim_token`` is ``None``.
        - ``"active"``: currently in progress by another worker; ``claim_token`` is ``None``.
        - ``"database_unavailable"``: DB error; ``claim_token`` is ``None``.

    Raises:
        RuntimeError: If ``settings.database_url`` is unset (callers must gate).
    """

    if settings.database_url is None:
        raise RuntimeError(
            "claim_github_webhook_delivery requires REVIEWGATE_DATABASE_URL",
        )

    engine = create_engine_from_settings(settings)
    if engine is None:
        raise RuntimeError(
            "create_engine_from_settings returned None despite database_url being set",
        )

    lease_seconds = (
        lease_timeout_seconds
        if lease_timeout_seconds is not None
        else getattr(settings, "webhook_delivery_lease_seconds", _DEFAULT_LEASE_TIMEOUT_SECONDS)
    )
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=lease_seconds)
    token = uuid.uuid4()

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        insert_stmt = pg_insert(WebhookDelivery).values(
            github_delivery_id=delivery_id,
            event_name=event_name,
            processed=False,
            claimed_at=now,
            claim_token=token,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["github_delivery_id"],
            set_={
                "event_name": insert_stmt.excluded.event_name,
                "claimed_at": now,
                "claim_token": token,
            },
            where=(
                (WebhookDelivery.processed.is_(False))
                & (WebhookDelivery.claimed_at < stale_cutoff)
            ),
        ).returning(WebhookDelivery.id)
        try:
            inserted_id = session.execute(upsert_stmt).scalar_one_or_none()
            if inserted_id is not None:
                session.commit()
                return ("claimed", token)

            # Conflict occurred and row was neither new nor stale/reclaimable:
            # Check if the delivery was already processed or is actively leased.
            select_stmt = select(WebhookDelivery.processed).where(
                WebhookDelivery.github_delivery_id == delivery_id
            )
            is_processed = session.execute(select_stmt).scalar_one_or_none()
            session.commit()
            if is_processed is True:
                return ("duplicate", None)
            return ("active", None)
        except OperationalError:
            session.rollback()
            return ("database_unavailable", None)


def release_github_webhook_delivery(
    settings: AppSettings,
    *,
    delivery_id: str,
    claim_token: uuid.UUID | None = None,
) -> None:
    """Release an in-flight delivery claim upon failure so it can be retried immediately.

    Sets ``claimed_at`` back to UNIX epoch only if ``claim_token`` matches the current
    claim (or when omitted) and the delivery remains unprocessed. This ensures a stale
    or timed-out request cannot release a lease acquired by a newer retry.

    Args:
        settings: Application settings (``REVIEWGATE_DATABASE_URL``).
        delivery_id: ``X-GitHub-Delivery`` header value.
        claim_token: Optional ownership token matching the current lease.
    """

    if settings.database_url is None:
        return

    engine = create_engine_from_settings(settings)
    if engine is None:
        return

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        epoch = datetime.fromtimestamp(0, tz=timezone.utc)
        conditions = [
            WebhookDelivery.github_delivery_id == delivery_id,
            WebhookDelivery.processed.is_(False),
        ]
        if claim_token is not None:
            conditions.append(WebhookDelivery.claim_token == claim_token)
        stmt = (
            update(WebhookDelivery)
            .where(*conditions)
            .values(claimed_at=epoch)
        )
        try:
            session.execute(stmt)
            session.commit()
        except OperationalError:
            session.rollback()


def mark_github_webhook_delivery_processed(
    settings: AppSettings,
    *,
    delivery_id: str,
    claim_token: uuid.UUID | None = None,
) -> None:
    """Mark a delivery as successfully processed in ``webhook_deliveries``.

    Args:
        settings: Application settings (``REVIEWGATE_DATABASE_URL``).
        delivery_id: ``X-GitHub-Delivery`` header value.
        claim_token: Optional ownership token matching the current lease.

    Raises:
        RuntimeError: If ``settings.database_url`` is unset (callers must gate).
        OperationalError: If database connection or commit fails.
    """

    if settings.database_url is None:
        raise RuntimeError(
            "mark_github_webhook_delivery_processed requires REVIEWGATE_DATABASE_URL",
        )

    engine = create_engine_from_settings(settings)
    if engine is None:
        raise RuntimeError(
            "create_engine_from_settings returned None despite database_url being set",
        )

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        conditions = [WebhookDelivery.github_delivery_id == delivery_id]
        if claim_token is not None:
            conditions.append(WebhookDelivery.claim_token == claim_token)
        stmt = (
            update(WebhookDelivery)
            .where(*conditions)
            .values(processed=True)
        )
        try:
            session.execute(stmt)
            session.commit()
        except OperationalError:
            session.rollback()
            raise
