"""Unit tests for :mod:`reviewgate.app.analysis.synchronize_debounce` (issue #45)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from reviewgate.app.analysis.synchronize_debounce import (
    parse_pull_request_repo_and_number,
    synchronize_debounce_key,
    synchronize_debounce_allows_enqueue,
    try_claim_synchronize_debounce,
)
from reviewgate.app.settings import AppSettings


def test_synchronize_debounce_key_normalizes_case() -> None:
    """Keys lower-case owner and repository short name for stable coalescing."""

    key = synchronize_debounce_key(owner="AcMe", repo="Foo", pull_number=42)
    assert key == "reviewgate:debounce:synchronize:acme/foo:42"


def test_try_claim_synchronize_debounce_respects_redis_set_nx() -> None:
    """First ``SET … NX`` wins; a falsy driver response means coalesced."""

    redis_mock = MagicMock()
    redis_mock.set.return_value = True
    assert try_claim_synchronize_debounce(
        redis_mock,
        owner="o",
        repo="r",
        pull_number=1,
    )
    redis_mock.set.assert_called_once()
    assert redis_mock.set.call_args.kwargs["nx"] is True
    assert redis_mock.set.call_args.kwargs["ex"] == 30
    assert redis_mock.set.call_args[0][0] == synchronize_debounce_key(
        owner="o",
        repo="r",
        pull_number=1,
    )

    redis_mock.set.return_value = None
    assert not try_claim_synchronize_debounce(
        redis_mock,
        owner="o",
        repo="r",
        pull_number=1,
    )


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

    assert synchronize_debounce_allows_enqueue(settings, payload) is True
    redis_mock.close.assert_called_once()
