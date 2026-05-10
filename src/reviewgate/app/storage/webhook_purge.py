"""Purge stale ``webhook_deliveries`` rows (``docs/DESIGN.md`` §16.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import delete
from sqlalchemy.orm import Session

from reviewgate.app.storage.models import WebhookDelivery

_DEFAULT_RETENTION_DAYS: Final[int] = 30


def purge_webhook_deliveries_older_than(
    session: Session,
    *,
    days: int = _DEFAULT_RETENTION_DAYS,
) -> int:
    """Delete rows whose ``created_at`` is older than ``days``.

    Args:
        session: Open ORM session (caller commits).
        days: Retention window in whole days (default 30 per §16.1).

    Returns:
        Number of rows deleted (driver-dependent; ``0`` when none matched).

    Note:
        Does not commit; callers should commit the session when appropriate.
    """

    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = session.execute(delete(WebhookDelivery).where(WebhookDelivery.created_at < cutoff))
    return int(result.rowcount or 0)
