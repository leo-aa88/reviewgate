"""Real-PostgreSQL concurrency checks for webhook delivery claims (issue #154).

``tests/test_webhooks_dedupe.py`` mocks the SQLAlchemy session and asserts on
compiled SQL text; that cannot prove PostgreSQL executes the
``INSERT ... ON CONFLICT DO UPDATE ... WHERE`` claim atomically. These tests run
the real functions against a migrated database with genuinely concurrent
connections, so a change that breaks claim atomicity fails here.

Skipped unless ``REVIEWGATE_DATABASE_URL`` points at a database migrated with
``alembic upgrade head`` (CI ``alembic-smoke`` job sets this).
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Final

import pytest
from sqlalchemy import create_engine, delete, select

from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.models import WebhookDelivery
from reviewgate.app.webhooks.dedupe import (
    ClaimResult,
    claim_github_webhook_delivery,
    mark_github_webhook_delivery_processed,
    release_github_webhook_delivery,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("REVIEWGATE_DATABASE_URL", "").strip(),
    reason=(
        "Set REVIEWGATE_DATABASE_URL to a PostgreSQL database after "
        "`alembic upgrade head` to run webhook claim concurrency checks."
    ),
)

_WORKERS: Final[int] = 10
_LEASE_SECONDS: Final[int] = 60


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


@pytest.fixture
def delivery_id(settings: AppSettings) -> Iterator[str]:
    did = f"it-{uuid.uuid4()}"
    yield did
    engine = create_engine(str(settings.database_url))
    with engine.begin() as conn:
        conn.execute(delete(WebhookDelivery).where(WebhookDelivery.github_delivery_id == did))
    engine.dispose()


def _claim(settings: AppSettings, delivery_id: str) -> tuple[ClaimResult, uuid.UUID | None]:
    return claim_github_webhook_delivery(
        settings,
        delivery_id=delivery_id,
        event_name="pull_request",
        lease_timeout_seconds=_LEASE_SECONDS,
    )


def _claim_concurrently(settings: AppSettings, delivery_id: str) -> list[ClaimResult]:
    barrier = threading.Barrier(_WORKERS)

    def worker() -> ClaimResult:
        barrier.wait()
        return _claim(settings, delivery_id)[0]

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = [pool.submit(worker) for _ in range(_WORKERS)]
        return [f.result() for f in futures]


def _is_processed(settings: AppSettings, delivery_id: str) -> bool:
    engine = create_engine(str(settings.database_url))
    with engine.connect() as conn:
        processed = conn.execute(
            select(WebhookDelivery.processed).where(
                WebhookDelivery.github_delivery_id == delivery_id
            )
        ).scalar_one()
    engine.dispose()
    return processed


def test_concurrent_claims_of_new_delivery_claim_exactly_once(
    settings: AppSettings,
    delivery_id: str,
) -> None:
    results = _claim_concurrently(settings, delivery_id)

    assert results.count("claimed") == 1
    assert results.count("active") == _WORKERS - 1


def test_concurrent_reclaims_of_released_delivery_claim_exactly_once(
    settings: AppSettings,
    delivery_id: str,
) -> None:
    """Exercises the ``ON CONFLICT DO UPDATE ... WHERE`` reclaim path under contention."""
    status, token = _claim(settings, delivery_id)
    assert status == "claimed"
    release_github_webhook_delivery(settings, delivery_id=delivery_id, claim_token=token)

    results = _claim_concurrently(settings, delivery_id)

    assert results.count("claimed") == 1
    assert results.count("active") == _WORKERS - 1


def test_release_then_redelivery_reclaims(settings: AppSettings, delivery_id: str) -> None:
    """Issue #154 repro: enqueue failure releases the claim, so a redelivery is not dropped."""
    status, token = _claim(settings, delivery_id)
    assert status == "claimed"
    assert _claim(settings, delivery_id)[0] == "active"

    release_github_webhook_delivery(settings, delivery_id=delivery_id, claim_token=token)

    retry_status, retry_token = _claim(settings, delivery_id)
    assert retry_status == "claimed"
    assert retry_token is not None and retry_token != token


def test_stale_token_cannot_release_or_mark_newer_claim(
    settings: AppSettings,
    delivery_id: str,
) -> None:
    _, stale_token = _claim(settings, delivery_id)
    release_github_webhook_delivery(settings, delivery_id=delivery_id, claim_token=stale_token)
    _, live_token = _claim(settings, delivery_id)

    # A straggler holding the superseded token must not free the newer lease...
    release_github_webhook_delivery(settings, delivery_id=delivery_id, claim_token=stale_token)
    assert _claim(settings, delivery_id)[0] == "active"

    # ...and its mark_processed write is discarded and reported as such.
    assert (
        mark_github_webhook_delivery_processed(
            settings, delivery_id=delivery_id, claim_token=stale_token
        )
        is False
    )
    assert _is_processed(settings, delivery_id) is False

    assert (
        mark_github_webhook_delivery_processed(
            settings, delivery_id=delivery_id, claim_token=live_token
        )
        is True
    )
    assert _is_processed(settings, delivery_id) is True
    assert _claim(settings, delivery_id)[0] == "duplicate"
