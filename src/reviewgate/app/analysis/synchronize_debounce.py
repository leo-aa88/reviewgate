"""Redis coalescing for rapid ``pull_request.synchronize`` bursts (issue #45).

Implements ``docs/DESIGN.md`` §13.7 debounce guidance: within a short TTL,
only the first synchronize delivery for a given repository + PR number may
proceed to enqueue; others acknowledge **202** without queueing work.

The key intentionally omits ``head_sha`` so force-push storms collapse into one
analysis enqueue while the window is active.
"""

from __future__ import annotations

from typing import Any, Final, Protocol

from reviewgate.app.redis_client import connect_redis
from reviewgate.app.settings import AppSettings

_DEBOUNCE_TTL_SECONDS: Final[int] = 30


class _RedisSetNX(Protocol):
    """Minimal Redis surface used for ``SET ... NX EX``."""

    def set(
        self,
        name: str,
        value: str | bytes,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        ...


def synchronize_debounce_key(*, owner: str, repo: str, pull_number: int) -> str:
    """Return the Redis key documenting the pending synchronize debounce slot."""

    own = owner.strip().lower()
    rep = repo.strip().lower()
    return f"reviewgate:debounce:synchronize:{own}/{rep}:{pull_number}"


def parse_pull_request_repo_and_number(payload: dict[str, Any]) -> tuple[str, str, int]:
    """Extract owner login, short repository name, and PR number from a webhook payload."""

    raw_num = payload.get("number")
    if isinstance(raw_num, bool) or not isinstance(raw_num, int):
        msg = "pull_request.number must be a positive integer"
        raise ValueError(msg)
    if raw_num < 1:
        msg = "pull_request.number must be a positive integer"
        raise ValueError(msg)

    repo = payload.get("repository")
    if not isinstance(repo, dict):
        msg = "pull_request payload is missing repository"
        raise ValueError(msg)

    owner_obj = repo.get("owner")
    if not isinstance(owner_obj, dict):
        msg = "repository.owner must be an object"
        raise ValueError(msg)
    login = owner_obj.get("login")
    short_name = repo.get("name")
    if not isinstance(login, str) or not login.strip():
        msg = "repository.owner.login must be a non-empty string"
        raise ValueError(msg)
    if not isinstance(short_name, str) or not short_name.strip():
        msg = "repository.name must be a non-empty string"
        raise ValueError(msg)

    return login.strip(), short_name.strip(), raw_num


def try_claim_synchronize_debounce(
    redis_client: _RedisSetNX,
    *,
    owner: str,
    repo: str,
    pull_number: int,
) -> bool:
    """Atomically reserve the debounce slot; return ``True`` when enqueue may proceed."""

    key = synchronize_debounce_key(
        owner=owner,
        repo=repo,
        pull_number=pull_number,
    )
    return bool(redis_client.set(key, "1", nx=True, ex=_DEBOUNCE_TTL_SECONDS))


def synchronize_debounce_allows_enqueue(
    settings: AppSettings,
    payload: dict[str, Any],
) -> bool:
    """Return ``False`` when a synchronize event should be coalesced (issue #45)."""

    action = payload.get("action")
    if action != "synchronize":
        return True

    owner, repo, pull_number = parse_pull_request_repo_and_number(payload)

    client = connect_redis(settings)
    if client is None:
        return True

    try:
        return try_claim_synchronize_debounce(
            client,
            owner=owner,
            repo=repo,
            pull_number=pull_number,
        )
    finally:
        client.close()
