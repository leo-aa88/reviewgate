"""GitHub ``POST /webhooks/github`` handler (``docs/DESIGN.md`` §13.3).

Validates ``X-Hub-Signature-256`` using the configured webhook secret, then
enqueues a lightweight Dramatiq message carrying delivery metadata so the HTTP
request returns quickly (§13.3). The actor module is imported inside the handler
so FastAPI lifespan can install the Redis broker first. Payload persistence and
delivery dedupe are handled in later issues (#34, #50).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import SecretStr

from reviewgate.app.settings import AppSettings

router = APIRouter()

_SHA256_PREFIX: Final[str] = "sha256="


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
    """Verify the webhook signature and enqueue a stub analysis job."""

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

    if settings.redis_url is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis URL is not configured for job enqueue",
        )

    delivery_id = request.headers.get("x-github-delivery", "")
    event_name = request.headers.get("x-github-event", "")

    from reviewgate.app.analysis.jobs import run_pr_analysis_stub

    run_pr_analysis_stub.send(
        {
            "github_delivery_id": delivery_id,
            "github_event": event_name,
        },
    )

    return Response(status_code=status.HTTP_202_ACCEPTED)
