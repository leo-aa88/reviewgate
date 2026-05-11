"""Tests for :mod:`reviewgate.app.github.comments` (issue #51)."""

from __future__ import annotations

import json
from typing import Final

import httpx
import pytest
from pydantic import SecretStr

pytest.importorskip("httpx")

from reviewgate.app.github.comments import (
    REVIEWGATE_REPORT_MARKER,
    find_reviewgate_report_comment_for_upsert,
    format_reviewgate_report_body,
    list_issue_comments,
    resolve_reviewgate_bot_login,
    upsert_reviewgate_report_issue_comment,
)
from reviewgate.app.github.client import GitHubRestError
from reviewgate.app.settings import AppSettings

_TOKEN: Final[SecretStr] = SecretStr("ghs_installation_token_example")
_BOT: Final[str] = "reviewgate-test[bot]"


def test_resolve_reviewgate_bot_login_prefers_explicit() -> None:
    settings = AppSettings(
        github_app_bot_login="  explicit[bot]  ",
        github_app_slug="ignored-slug",
    )
    assert resolve_reviewgate_bot_login(settings) == "explicit[bot]"


def test_resolve_reviewgate_bot_login_from_slug() -> None:
    settings = AppSettings(github_app_slug=" my-app ")
    assert resolve_reviewgate_bot_login(settings) == "my-app[bot]"


def test_resolve_reviewgate_bot_login_requires_config() -> None:
    with pytest.raises(ValueError, match="REVIEWGATE_GITHUB_APP"):
        resolve_reviewgate_bot_login(AppSettings())


def test_format_reviewgate_report_body_prepends_marker() -> None:
    out = format_reviewgate_report_body("## Hello")
    assert out.startswith(REVIEWGATE_REPORT_MARKER + "\n\n")
    assert "## Hello" in out


def test_format_reviewgate_report_body_idempotent_when_marked() -> None:
    raw = f"{REVIEWGATE_REPORT_MARKER}\n\n## Hi"
    assert format_reviewgate_report_body(raw) == raw


def test_find_comment_requires_bot_and_marker() -> None:
    comments = [
        {
            "id": 1,
            "user": {"login": "human"},
            "body": f"{REVIEWGATE_REPORT_MARKER}\nold",
        },
        {
            "id": 2,
            "user": {"login": _BOT},
            "body": "no marker",
        },
        {
            "id": 3,
            "user": {"login": _BOT},
            "body": f"{REVIEWGATE_REPORT_MARKER}\nwinner",
        },
        {
            "id": 4,
            "user": {"login": "other[bot]"},
            "body": f"{REVIEWGATE_REPORT_MARKER}\nother",
        },
    ]
    found = find_reviewgate_report_comment_for_upsert(comments, bot_login=_BOT)
    assert found is not None
    assert found["id"] == 3


def test_list_issue_comments_paginates() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/issues/5/comments" in str(request.url)
        page = int(request.url.params.get("page", "1"))
        pages.append(page)
        if page == 1:
            return httpx.Response(200, json=[{"id": i} for i in range(100)])
        return httpx.Response(200, json=[{"id": 100}])

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        rows = list_issue_comments(
            _TOKEN,
            owner="o",
            repo="p",
            issue_number=5,
            http_client=client,
        )
    assert len(rows) == 101
    assert pages == [1, 2]


def test_upsert_creates_when_no_matching_comment() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "user": {"login": "human"},
                        "body": f"{REVIEWGATE_REPORT_MARKER}\nignore",
                    },
                ],
            )
        if request.method == "POST":
            payload = json.loads(request.content.decode())
            assert REVIEWGATE_REPORT_MARKER in str(payload.get("body", ""))
            return httpx.Response(201, json={"id": 99})
        return httpx.Response(400, json={"message": "unexpected"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = upsert_reviewgate_report_issue_comment(
            _TOKEN,
            owner="acme",
            repo="r",
            issue_number=3,
            body_markdown="## PASS",
            bot_login=_BOT,
            http_client=client,
        )
    assert result.comment_id == 99
    assert result.updated is False
    assert paths[0].endswith("/issues/3/comments")
    assert paths[1].endswith("/issues/3/comments")


def test_upsert_patches_when_bot_authored_marker_comment() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 10,
                        "user": {"login": _BOT},
                        "body": f"{REVIEWGATE_REPORT_MARKER}\nold",
                    },
                ],
            )
        if request.method == "PATCH":
            payload = json.loads(request.content.decode())
            body = str(payload.get("body", ""))
            assert REVIEWGATE_REPORT_MARKER in body
            assert "new" in body
            return httpx.Response(200, json={"id": 10})
        return httpx.Response(400, json={"message": "bad"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = upsert_reviewgate_report_issue_comment(
            _TOKEN,
            owner="acme",
            repo="r",
            issue_number=3,
            body_markdown="new",
            bot_login=_BOT,
            http_client=client,
        )
    assert result.comment_id == 10
    assert result.updated is True
    assert methods == ["GET", "PATCH"]


def test_upsert_patch_404_is_not_retriable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 77,
                        "user": {"login": _BOT},
                        "body": f"{REVIEWGATE_REPORT_MARKER}\nx",
                    },
                ],
            )
        return httpx.Response(404, json={"message": "gone"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(GitHubRestError) as exc:
            upsert_reviewgate_report_issue_comment(
                _TOKEN,
                owner="a",
                repo="b",
                issue_number=1,
                body_markdown="z",
                bot_login=_BOT,
                http_client=client,
            )
    assert exc.value.retriable is False
