"""GitHub ``POST /webhooks/github`` handler (``docs/DESIGN.md`` §13.3).

Validates ``X-Hub-Signature-256`` using the configured webhook secret, then
routes by ``X-GitHub-Event`` per ``docs/DESIGN.md`` §13.2: ``ping`` and
installation events return **202** without Redis; unsupported events return
**204**; ``pull_request`` actions in ``opened`` / ``synchronize`` / ``reopened`` enqueue
a stub job; ``edited`` enqueues only when ``changes`` touches ``title``,
``body``, or ``base`` (§13.2). The actor module is imported
only on the enqueue path after
:func:`reviewgate.app.analysis.broker_install.install_redis_broker` runs.
Delivery dedupe uses ``webhook_deliveries`` when ``REVIEWGATE_DATABASE_URL`` is
set (issue #34). Payload persistence is handled in later issues (#50).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Final

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import SecretStr
from starlette.concurrency import run_in_threadpool

from reviewgate.app.analysis.broker_install import install_redis_broker
from reviewgate.app.settings import AppSettings
from reviewgate.app.webhooks.dedupe import claim_github_webhook_delivery

router = APIRouter()

_SHA256_PREFIX: Final[str] = "sha256="

# ``docs/DESIGN.md`` §13.2 — PR events that may enqueue analysis.
_PULL_REQUEST_ANALYSIS_ACTIONS: Final[frozenset[str]] = frozenset(
    {"opened", "synchronize", "edited", "reopened"},
)

# Subset of ``pull_request`` ``changes`` keys that affect reviewability (§13.2).
_PULL_REQUEST_EDIT_RELEVANT_CHANGES: Final[frozenset[str]] = frozenset(
    {"title", "body", "base"},
)

# Lifecycle probes GitHub sends during setup or installation management; no
# analysis job.
_ACK_EVENTS_NO_QUEUE: Final[frozenset[str]] = frozenset(
    {"ping", "installation", "installation_repositories"},
)


def _verify_signature_sha256(
    body: bytes,
    signature_header: str | None,
    secret: SecretStr,
) -> bool:
    """Return ``True`` when ``X-Hub-Signature-256`` matches the raw body."""

    if signature_header is None or not signature_header.startswith(_SHA256_PREFIX):
        return False
    digest = hmac.new(
        secret.get_secret_value().encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    expected = f"{_SHA256_PREFIX}{digest}"
    return hmac.compare_digest(signature_header, expected)


@router.post("/webhooks/github")
async def github_webhook(request: Request) -> Response:
    """Verify the signature, then acknowledge or enqueue per ``DESIGN.md`` §13.2."""

    settings = AppSettings()
    body = await request.body()

    if settings.github_webhook_secret is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub webhook secret is not configured",
        )

    signature = request.headers.get("x-hub-signature-256")
    if not _verify_signature_sha256(body, signature, settings.github_webhook_secret):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing GitHub webhook signature",
        )

    delivery_id = request.headers.get("x-github-delivery", "")
    event_name = request.headers.get("x-github-event", "")

    if event_name in _ACK_EVENTS_NO_QUEUE:
        return Response(status_code=status.HTTP_202_ACCEPTED)

    if event_name != "pull_request":
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    try:
        payload_obj: object = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="pull_request webhook body must be JSON",
        ) from exc

    if not isinstance(payload_obj, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="pull_request webhook body must be a JSON object",
        )

    action = payload_obj.get("action")
    if not isinstance(action, str) or action not in _PULL_REQUEST_ANALYSIS_ACTIONS:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if action == "edited":
        changes = payload_obj.get("changes")
        if not isinstance(changes, dict):
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if not _PULL_REQUEST_EDIT_RELEVANT_CHANGES.intersection(changes):
            return Response(status_code=status.HTTP_204_NO_CONTENT)

    if not delivery_id.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Delivery",
        )

    if settings.redis_url is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis URL is not configured for job enqueue",
        )

    claim_result = await run_in_threadpool(
        claim_github_webhook_delivery,
        settings,
        delivery_id=delivery_id,
        event_name=event_name,
    )
    if claim_result == "duplicate":
        return Response(status_code=status.HTTP_202_ACCEPTED)

    install_redis_broker(settings)

    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub.send(
        {
            "github_delivery_id": delivery_id,
            "github_event": event_name,
            "github_pull_request_action": action,
        },
    )

    return Response(status_code=status.HTTP_202_ACCEPTED)
