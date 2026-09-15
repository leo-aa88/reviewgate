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


_RELEASE_IF_MATCH_LUA: Final[str] = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
"""


class _RedisSetNX(Protocol):
    """Minimal Redis surface used for ``SET ... NX EX`` and ``GET``."""

    def set(
        self,
        name: str,
        value: str | bytes,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        ...

    def get(self, name: str) -> str | bytes | None:
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
    delivery_id: str = "1",
) -> bool:
    """Atomically reserve the debounce slot; return ``True`` when enqueue may proceed.

    If the slot is already occupied, checks whether the current slot value equals
    ``delivery_id``. If it matches, this request is an owner retry for the same delivery
    and enqueue is allowed to proceed. If a different delivery occupies the slot,
    returns ``False`` so the delivery is coalesced.
    """

    key = synchronize_debounce_key(
        owner=owner,
        repo=repo,
        pull_number=pull_number,
    )
    if bool(redis_client.set(key, delivery_id, nx=True, ex=_DEBOUNCE_TTL_SECONDS)):
        return True

    existing = redis_client.get(key)
    if isinstance(existing, bytes):
        existing = existing.decode("utf-8", errors="replace")

    return bool(existing is not None and existing == delivery_id)


def try_release_synchronize_debounce(
    redis_client: Any,
    *,
    owner: str,
    repo: str,
    pull_number: int,
    delivery_id: str,
) -> bool:
    """Atomically delete the debounce key only if its current value equals ``delivery_id``."""

    key = synchronize_debounce_key(
        owner=owner,
        repo=repo,
        pull_number=pull_number,
    )
    return bool(redis_client.eval(_RELEASE_IF_MATCH_LUA, 1, key, delivery_id))


def synchronize_debounce_allows_enqueue(
    settings: AppSettings,
    payload: dict[str, Any],
    *,
    delivery_id: str = "1",
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
            delivery_id=delivery_id,
        )
    finally:
        client.close()


def release_synchronize_debounce(
    settings: AppSettings,
    payload: dict[str, Any],
    *,
    delivery_id: str,
) -> bool:
    """Atomically release synchronize debounce slot if it was reserved by this delivery.

    Returns ``True`` when the matching key was deleted, or ``False`` when the slot
    was not reserved by this delivery, payload was not a synchronize event, or Redis
    is unconfigured.

    Raises:
        redis.exceptions.RedisError: When Redis communication or Lua evaluation fails.
    """

    action = payload.get("action")
    if action != "synchronize":
        return False

    try:
        owner, repo, pull_number = parse_pull_request_repo_and_number(payload)
    except ValueError:
        return False

    client = connect_redis(settings)
    if client is None:
        return False

    try:
        return try_release_synchronize_debounce(
            client,
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            delivery_id=delivery_id,
        )
    finally:
        client.close()
