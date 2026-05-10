"""GitHub webhook delivery dedupe using ``webhook_deliveries`` (``docs/DESIGN.md`` §13.3, §16.1)."""

from __future__ import annotations

from typing import Literal

from sqlalchemy.exc import IntegrityError, OperationalError

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
    """Insert a delivery row or detect an existing one (unique ``github_delivery_id``).

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
        session.add(
            WebhookDelivery(
                github_delivery_id=delivery_id,
                event_name=event_name,
            ),
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return "duplicate"
        except OperationalError:
            session.rollback()
            return "database_unavailable"
        return "claimed"
