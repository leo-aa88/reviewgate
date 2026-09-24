"""Unit tests for :mod:`reviewgate.app.analysis.synchronize_debounce` (issue #45)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import redis.exceptions

from reviewgate.app.analysis.synchronize_debounce import (
    _RELEASE_IF_MATCH_LUA,
    parse_pull_request_repo_and_number,
    release_synchronize_debounce,
    synchronize_debounce_allows_enqueue,
    synchronize_debounce_key,
    try_claim_synchronize_debounce,
    try_release_synchronize_debounce,
)
from reviewgate.app.settings import AppSettings


def test_synchronize_debounce_key_normalizes_case() -> None:
    """Keys lower-case owner and repository short name for stable coalescing."""

    key = synchronize_debounce_key(owner="AcMe", repo="Foo", pull_number=42)
    assert key == "reviewgate:debounce:synchronize:acme/foo:42"


def test_try_claim_synchronize_debounce_respects_redis_set_nx() -> None:
    """First ``SET … NX`` wins; a falsy response checks existing value."""

    redis_mock = MagicMock()
    redis_mock.set.return_value = True
    assert try_claim_synchronize_debounce(
        redis_mock,
        owner="o",
        repo="r",
        pull_number=1,
        delivery_id="deliv-1",
    )
    redis_mock.set.assert_called_once()
    assert redis_mock.set.call_args.kwargs["nx"] is True
    assert redis_mock.set.call_args.kwargs["ex"] == 30
    assert redis_mock.set.call_args[0][0] == synchronize_debounce_key(
        owner="o",
        repo="r",
        pull_number=1,
    )
    assert redis_mock.set.call_args[0][1] == "deliv-1"

    # When SET NX returns False and GET returns a different delivery, it is coalesced (False)
    redis_mock.set.return_value = None
    redis_mock.get.return_value = "deliv-other"
    assert not try_claim_synchronize_debounce(
        redis_mock,
        owner="o",
        repo="r",
        pull_number=1,
        delivery_id="deliv-1",
    )


def test_try_claim_synchronize_debounce_owner_retry() -> None:
    """When SET NX returns False but GET returns the same delivery ID, treat as owner retry."""

    redis_mock = MagicMock()
    redis_mock.set.return_value = None
    redis_mock.get.return_value = "deliv-same"

    assert try_claim_synchronize_debounce(
        redis_mock,
        owner="o",
        repo="r",
        pull_number=1,
        delivery_id="deliv-same",
    ) is True
    redis_mock.get.assert_called_once_with(
        synchronize_debounce_key(owner="o", repo="r", pull_number=1),
    )


def test_try_claim_synchronize_debounce_owner_retry_bytes() -> None:
    """Owner retry comparison handles bytes response from Redis driver."""

    redis_mock = MagicMock()
    redis_mock.set.return_value = None
    redis_mock.get.return_value = b"deliv-bytes"

    assert try_claim_synchronize_debounce(
        redis_mock,
        owner="o",
        repo="r",
        pull_number=1,
        delivery_id="deliv-bytes",
    ) is True


def test_try_claim_synchronize_debounce_expired_slot_returns_false() -> None:
    """When SET NX returns False and GET returns None (key expired between calls), returns False."""

    redis_mock = MagicMock()
    redis_mock.set.return_value = None
    redis_mock.get.return_value = None

    assert try_claim_synchronize_debounce(
        redis_mock,
        owner="o",
        repo="r",
        pull_number=1,
        delivery_id="deliv-1",
    ) is False


def test_try_release_synchronize_debounce_atomic_eval() -> None:
    """Atomic compare-and-delete invokes Lua script with matching delivery id."""

    redis_mock = MagicMock()
    redis_mock.eval.return_value = 1
    key = synchronize_debounce_key(owner="o", repo="r", pull_number=1)

    assert try_release_synchronize_debounce(
        redis_mock,
        owner="o",
        repo="r",
        pull_number=1,
        delivery_id="deliv-mine",
    ) is True
    redis_mock.eval.assert_called_once_with(_RELEASE_IF_MATCH_LUA, 1, key, "deliv-mine")

    redis_mock.eval.return_value = 0
    assert try_release_synchronize_debounce(
        redis_mock,
        owner="o",
        repo="r",
        pull_number=1,
        delivery_id="deliv-other",
    ) is False


def test_parse_pull_request_repo_and_number_success() -> None:
    """Happy path mirrors GitHub ``pull_request`` webhook shape."""

    payload = {
        "number": 7,
        "repository": {
            "name": "reviewgate",
            "owner": {"login": "leo-aa88"},
        },
    }
    assert parse_pull_request_repo_and_number(payload) == ("leo-aa88", "reviewgate", 7)


@pytest.mark.parametrize(
    "payload",
    [
        {"number": 1},
        {"number": 0, "repository": {"name": "r", "owner": {"login": "o"}}},
        {"number": True, "repository": {"name": "r", "owner": {"login": "o"}}},
        {"number": 1, "repository": "bad"},
        {"number": 1, "repository": {"name": "r"}},
        {"number": 1, "repository": {"name": "r", "owner": "bad"}},
        {"number": 1, "repository": {"name": "", "owner": {"login": "o"}}},
        {
            "number": 1,
            "repository": {
                "name": "r",
                "owner": {"login": ""},
            },
        },
    ],
)
def test_parse_pull_request_repo_and_number_rejects(payload: dict) -> None:
    """Malformed payloads raise ``ValueError`` for a 400 response upstream."""

    with pytest.raises(ValueError):
        parse_pull_request_repo_and_number(payload)


def test_synchronize_debounce_allows_enqueue_skips_redis_for_non_synchronize() -> None:
    """Only ``action == synchronize`` touches Redis."""

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    payload = {"action": "opened", "number": 1}
    assert synchronize_debounce_allows_enqueue(settings, payload) is True


def test_synchronize_debounce_allows_enqueue_uses_connect_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synchronize path delegates to :func:`connect_redis` and ``SET NX``."""

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    payload = {
        "action": "synchronize",
        "number": 3,
        "repository": {"name": "r", "owner": {"login": "o"}},
    }

    redis_mock = MagicMock()
    redis_mock.set.return_value = True
    redis_mock.close = MagicMock()

    monkeypatch.setattr(
        "reviewgate.app.analysis.synchronize_debounce.connect_redis",
        lambda _s: redis_mock,
    )

    assert synchronize_debounce_allows_enqueue(settings, payload, delivery_id="d3") is True
    redis_mock.close.assert_called_once()


def test_release_synchronize_debounce_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """release_synchronize_debounce atomically releases reservation when matching."""

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    payload = {
        "action": "synchronize",
        "number": 3,
        "repository": {"name": "r", "owner": {"login": "o"}},
    }

    redis_mock = MagicMock()
    redis_mock.eval.return_value = 1
    redis_mock.close = MagicMock()

    monkeypatch.setattr(
        "reviewgate.app.analysis.synchronize_debounce.connect_redis",
        lambda _s: redis_mock,
    )

    assert release_synchronize_debounce(settings, payload, delivery_id="deliv-match") is True
    redis_mock.eval.assert_called_once()
    redis_mock.close.assert_called_once()


def test_release_synchronize_debounce_does_not_delete_other_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """release_synchronize_debounce returns False when key has a different delivery ID."""

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    payload = {
        "action": "synchronize",
        "number": 3,
        "repository": {"name": "r", "owner": {"login": "o"}},
    }

    redis_mock = MagicMock()
    redis_mock.eval.return_value = 0
    redis_mock.close = MagicMock()

    monkeypatch.setattr(
        "reviewgate.app.analysis.synchronize_debounce.connect_redis",
        lambda _s: redis_mock,
    )

    assert release_synchronize_debounce(settings, payload, delivery_id="deliv-mismatch") is False
    redis_mock.eval.assert_called_once()
    redis_mock.close.assert_called_once()


def test_release_synchronize_debounce_non_synchronize_skips_redis() -> None:
    """release_synchronize_debounce skips Redis when action is not synchronize."""

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    payload = {"action": "opened", "number": 1}
    assert release_synchronize_debounce(settings, payload, delivery_id="d1") is False


def test_release_synchronize_debounce_redis_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """release_synchronize_debounce raises RedisError when Redis fails."""

    settings = AppSettings(redis_url="redis://127.0.0.1:6379/0")
    payload = {
        "action": "synchronize",
        "number": 3,
        "repository": {"name": "r", "owner": {"login": "o"}},
    }

    redis_mock = MagicMock()
    redis_mock.eval.side_effect = redis.exceptions.ConnectionError("Redis unreachable")
    redis_mock.close = MagicMock()

    monkeypatch.setattr(
        "reviewgate.app.analysis.synchronize_debounce.connect_redis",
        lambda _s: redis_mock,
    )

    with pytest.raises(redis.exceptions.RedisError):
        release_synchronize_debounce(settings, payload, delivery_id="d1")

    redis_mock.close.assert_called_once()
