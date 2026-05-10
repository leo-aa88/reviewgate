"""Tests for :mod:`reviewgate.app.github.client` (issue #40)."""

from __future__ import annotations

from typing import Final

import httpx
import pytest
from pydantic import SecretStr

pytest.importorskip("httpx")

from reviewgate.app.github.client import (
    GitHubRestError,
    fetch_pull_request,
    fetch_pull_request_files,
)

_TOKEN: Final[SecretStr] = SecretStr("ghs_installation_token_example")


def test_fetch_pull_request_returns_json_object() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer ghs_installation_token_example"
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert request.url.path.endswith("/repos/acme/r/pulls/7")
        return httpx.Response(
            200,
            json={"number": 7, "title": "Add widget", "state": "open"},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        body = fetch_pull_request(
            _TOKEN,
            owner="acme",
            repo="r",
            pull_number=7,
            http_client=client,
        )
    assert body["title"] == "Add widget"
    assert len(calls) == 1


def test_fetch_pull_request_files_paginates_per_page_100() -> None:
    """Second page is requested when the first returns 100 rows."""

    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        per_page = int(request.url.params.get("per_page", "0"))
        pages.append(page)
        assert per_page == 100
        if page == 1:
            return httpx.Response(200, json=[{"filename": f"f{i}.py"} for i in range(100)])
        return httpx.Response(200, json=[{"filename": "last.py"}])

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        files = fetch_pull_request_files(
            _TOKEN,
            owner="o",
            repo="p",
            pull_number=1,
            http_client=client,
        )
    assert len(files) == 101
    assert pages == [1, 2]


def test_fetch_pull_request_503_is_retriable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "under pressure"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(GitHubRestError) as excinfo:
            fetch_pull_request(
                _TOKEN,
                owner="a",
                repo="b",
                pull_number=1,
                http_client=client,
            )
    err = excinfo.value
    assert err.retriable is True
    assert err.status_code == 503


def test_fetch_pull_request_404_is_not_retriable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(GitHubRestError) as excinfo:
            fetch_pull_request(
                _TOKEN,
                owner="a",
                repo="b",
                pull_number=99,
                http_client=client,
            )
    assert excinfo.value.retriable is False
    assert excinfo.value.status_code == 404


def test_fetch_pull_request_403_rate_limited_is_retriable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0"},
            json={"message": "API rate limit exceeded"},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(GitHubRestError) as excinfo:
            fetch_pull_request(
                _TOKEN,
                owner="a",
                repo="b",
                pull_number=1,
                http_client=client,
            )
    assert excinfo.value.retriable is True


def test_fetch_pull_request_transport_error_is_retriable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(GitHubRestError) as excinfo:
            fetch_pull_request(
                _TOKEN,
                owner="a",
                repo="b",
                pull_number=1,
                http_client=client,
            )
    assert excinfo.value.retriable is True
    assert excinfo.value.status_code is None


def test_fetch_pull_request_rejects_invalid_owner() -> None:
    with pytest.raises(ValueError, match="invalid GitHub owner"):
        fetch_pull_request(_TOKEN, owner="bad/name", repo="r", pull_number=1)
