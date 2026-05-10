"""GitHub webhook delivery dedupe using ``webhook_deliveries`` (``docs/DESIGN.md`` §13.3, §16.1)."""

from __future__ import annotations

from typing import Literal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError

from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.models import WebhookDelivery

ClaimResult = Literal["claimed", "duplicate", "database_unavailable"]


def claim_github_webhook_delivery(
    settings: AppSettings,
    *,
    delivery_id: str,
    event_name: str,
) -> ClaimResult:
    """Atomically claim a delivery id using ``INSERT ... ON CONFLICT DO NOTHING``.

    Args:
        settings: Application settings (``REVIEWGATE_DATABASE_URL``).
        delivery_id: ``X-GitHub-Delivery`` header value.
        event_name: ``X-GitHub-Event`` header value.

    Returns:
        ``claimed`` when a new row was committed, ``duplicate`` when the delivery
        id was already recorded, or ``database_unavailable`` when Postgres is
        unreachable so the HTTP layer can surface a retryable **503**.

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

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        insert_stmt = (
            pg_insert(WebhookDelivery)
            .values(
                github_delivery_id=delivery_id,
                event_name=event_name,
                processed=False,
            )
            .on_conflict_do_nothing(index_elements=["github_delivery_id"])
            .returning(WebhookDelivery.id)
        )
        try:
            inserted_id = session.execute(insert_stmt).scalar_one_or_none()
            session.commit()
        except OperationalError:
            session.rollback()
            return "database_unavailable"
        return "claimed" if inserted_id is not None else "duplicate"
