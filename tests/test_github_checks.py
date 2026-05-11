"""Tests for :mod:`reviewgate.app.github.checks` (issue #53)."""

from __future__ import annotations

import json
from typing import Final

import httpx
import pytest
from pydantic import SecretStr

pytest.importorskip("httpx")

from reviewgate.app.github.checks import (
    create_completed_reviewability_check_run,
    reviewability_check_conclusion,
)
from reviewgate.core.config import StatusCheck
from reviewgate.core.schemas import Reviewability

_TOKEN: Final[SecretStr] = SecretStr("ghs_installation_token_example")
_SHA40: Final[str] = "a" * 40


@pytest.mark.parametrize(
    ("verdict", "warn_blocks", "expected"),
    [
        ("PASS", False, "success"),
        ("PASS", True, "success"),
        ("FAIL", False, "failure"),
        ("FAIL", True, "failure"),
        ("WARN", False, "neutral"),
        ("WARN", True, "failure"),
    ],
)
def test_reviewability_check_conclusion_table(
    verdict: Reviewability,
    warn_blocks: bool,
    expected: str,
) -> None:
    assert (
        reviewability_check_conclusion(
            verdict,
            warn_blocks_merge=warn_blocks,
        )
        == expected
    )


def test_create_check_run_posts_completed_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/check-runs")
        payload = json.loads(request.content.decode())
        captured.update(payload)
        return httpx.Response(201, json={"id": 424242})

    sc = StatusCheck(
        enabled=True,
        name="reviewgate/reviewability",
        warn_blocks_merge=False,
    )
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        rid = create_completed_reviewability_check_run(
            _TOKEN,
            owner="o",
            repo="p",
            head_sha=_SHA40,
            reviewability="WARN",
            status_check=sc,
            http_client=client,
        )
    assert rid == 424242
    assert captured["name"] == "reviewgate/reviewability"
    assert captured["head_sha"] == _SHA40
    assert captured["status"] == "completed"
    assert captured["conclusion"] == "neutral"
    out = captured["output"]
    assert isinstance(out, dict)
    assert "WARN" in str(out.get("summary", ""))


def test_create_check_run_disabled_raises() -> None:
    sc = StatusCheck(enabled=False)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ValueError, match="enabled"):
            create_completed_reviewability_check_run(
                _TOKEN,
                owner="o",
                repo="p",
                head_sha=_SHA40,
                reviewability="PASS",
                status_check=sc,
                http_client=client,
            )


def test_create_check_run_short_sha_raises() -> None:
    sc = StatusCheck(enabled=True)
    with pytest.raises(ValueError, match="7 characters"):
        create_completed_reviewability_check_run(
            _TOKEN,
            owner="o",
            repo="p",
            head_sha="abc",
            reviewability="PASS",
            status_check=sc,
        )
