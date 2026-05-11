"""Tests for :mod:`reviewgate.app.github.labels` (issue #52)."""

from __future__ import annotations

import json
from typing import Final

import httpx
import pytest
from pydantic import SecretStr

pytest.importorskip("httpx")

from reviewgate.app.github.client import GitHubRestError
from reviewgate.app.github.labels import (
    ensure_reviewgate_labels_exist,
    list_issue_label_names,
    managed_label_names,
    sync_reviewgate_labels_on_issue,
)
from reviewgate.core.config import Labels

_TOKEN: Final[SecretStr] = SecretStr("ghs_installation_token_example")


def test_managed_label_names_includes_config_fields() -> None:
    labels = Labels(
        **{
            "pass": "p-pass",
            "warn": "p-warn",
            "fail": "p-fail",
            "too_large": "p-big",
            "missing_context": "p-miss",
            "risky_change": "p-risk",
            "needs_split": "p-split",
        },
    )
    names = managed_label_names(labels)
    assert names == {
        "p-pass",
        "p-warn",
        "p-fail",
        "p-big",
        "p-miss",
        "p-risk",
        "p-split",
    }


def test_sync_rejects_unknown_desired_label() -> None:
    with pytest.raises(ValueError, match="outside managed"):
        sync_reviewgate_labels_on_issue(
            _TOKEN,
            owner="o",
            repo="p",
            issue_number=1,
            desired_labels=["not-a-reviewgate-label"],
            labels_config=Labels(),
        )


def test_list_issue_label_names_parses_names() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/issues/9/labels")
        return httpx.Response(
            200,
            json=[
                {"name": "a", "color": "000000"},
                {"name": "b", "color": "ffffff"},
            ],
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        names = list_issue_label_names(
            _TOKEN,
            owner="acme",
            repo="r",
            issue_number=9,
            http_client=client,
        )
    assert names == ["a", "b"]


def test_ensure_reviewgate_labels_exist_creates_on_404() -> None:
    paths: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/labels/missing-context"):
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST" and request.url.path.endswith("/labels"):
            payload = json.loads(request.content.decode())
            assert payload["name"] == "missing-context"
            return httpx.Response(201, json={"name": "missing-context"})
        if request.method == "GET":
            return httpx.Response(200, json={"name": "exists", "color": "ffffff"})
        return httpx.Response(500, json={"message": "unexpected"})

    transport = httpx.MockTransport(handler)
    cfg = Labels()
    with httpx.Client(transport=transport) as client:
        ensure_reviewgate_labels_exist(
            _TOKEN,
            owner="o",
            repo="p",
            labels_config=cfg,
            http_client=client,
        )
    posts = [p for p in paths if p[0] == "POST"]
    assert len(posts) == 1


def test_sync_removes_managed_stale_and_preserves_user_labels() -> None:
    """Only ReviewGate-managed names are removed; arbitrary labels stay."""

    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url.path)))
        if request.method == "GET" and "/issues/3/labels" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {"name": "reviewability-warn"},
                    {"name": "priority-high"},
                ],
            )
        if request.method == "DELETE":
            assert request.url.path.endswith("/issues/3/labels/reviewability-warn")
            return httpx.Response(204)
        if request.method == "POST" and request.url.path.endswith("/issues/3/labels"):
            payload = json.loads(request.content.decode())
            assert payload["labels"] == ["reviewability-pass"]
            return httpx.Response(200, json=payload)
        return httpx.Response(400, json={"message": "bad"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        sync_reviewgate_labels_on_issue(
            _TOKEN,
            owner="acme",
            repo="demo",
            issue_number=3,
            desired_labels=["reviewability-pass"],
            labels_config=Labels(),
            http_client=client,
        )

    assert any(c[0] == "GET" for c in calls)
    assert any(c[0] == "DELETE" for c in calls)
    assert any(c[0] == "POST" for c in calls)


def test_ensure_label_create_422_is_treated_as_race() -> None:
    """Concurrent label creation returns 422; ensure must not crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"message": "missing"})
        if request.method == "POST":
            return httpx.Response(422, json={"message": "already exists"})
        return httpx.Response(400, json={})

    transport = httpx.MockTransport(handler)
    # Single-label managed set trick: use Labels where only one unique? All 7 fields differ.
    # Loop hits 7 GET 404 + 7 POST 422 — all should pass.
    with httpx.Client(transport=transport) as client:
        ensure_reviewgate_labels_exist(
            _TOKEN,
            owner="o",
            repo="p",
            labels_config=Labels(),
            http_client=client,
        )


def test_sync_delete_404_is_race_tolerant() -> None:
    """Stale list + concurrent remover can yield DELETE 404; sync must succeed."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/issues/4/labels" in request.url.path:
            return httpx.Response(200, json=[{"name": "reviewability-warn"}])
        if request.method == "DELETE":
            return httpx.Response(404, json={"message": "Label does not exist"})
        if request.method == "POST":
            return httpx.Response(200, json={"labels": ["reviewability-pass"]})
        return httpx.Response(400, json={"message": "unexpected"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        sync_reviewgate_labels_on_issue(
            _TOKEN,
            owner="acme",
            repo="demo",
            issue_number=4,
            desired_labels=["reviewability-pass"],
            labels_config=Labels(),
            http_client=client,
        )


def test_list_issue_labels_404_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "nope"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(GitHubRestError) as exc:
            list_issue_label_names(
                _TOKEN,
                owner="a",
                repo="b",
                issue_number=2,
                http_client=client,
            )
    assert exc.value.retriable is False
