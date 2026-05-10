"""Tests for :mod:`reviewgate.app.analysis.config_hash` (issue #43)."""

from __future__ import annotations

import base64
from typing import Final

import httpx
import pytest
from pydantic import SecretStr

pytest.importorskip("httpx")

from reviewgate.app.analysis.config_hash import (
    compute_config_hash_from_yaml,
    fetch_reviewgate_yml_and_config_hash,
)
from reviewgate.app.github.client import fetch_repository_text_file_contents

_TOKEN: Final[SecretStr] = SecretStr("ghs_token")


def test_compute_config_hash_stable_for_identical_yaml() -> None:
    yaml = "version: 1\nmode: app\n"
    h1, r1 = compute_config_hash_from_yaml(yaml)
    h2, r2 = compute_config_hash_from_yaml(yaml)
    assert h1 == h2
    assert r1.config.mode == r2.config.mode


def test_compute_config_hash_same_for_malformed_yaml_defaults() -> None:
    """Two invalid documents that both fall back to defaults share a hash."""

    h1, _ = compute_config_hash_from_yaml("not: [")
    h2, _ = compute_config_hash_from_yaml("also: bad: yaml: [[")
    assert h1 == h2


def test_fetch_reviewgate_yml_and_config_hash_missing_file_uses_defaults() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/contents/.reviewgate.yml" in str(request.url)
        assert request.url.params.get("ref") == "main"
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        digest, result = fetch_reviewgate_yml_and_config_hash(
            _TOKEN,
            owner="o",
            repo="r",
            base_ref="main",
            http_client=client,
        )
    digest2, result2 = compute_config_hash_from_yaml(None)
    assert digest == digest2
    assert result.warnings == result2.warnings


def test_fetch_repository_text_file_contents_decodes_base64() -> None:
    text = "version: 1\nmode: both\n"
    b64 = base64.b64encode(text.encode()).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": b64,
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        got = fetch_repository_text_file_contents(
            _TOKEN,
            owner="o",
            repo="r",
            path=".reviewgate.yml",
            git_ref="main",
            http_client=client,
        )
    assert got == text


def test_fetch_reviewgate_yml_rejects_empty_base_ref() -> None:
    with pytest.raises(ValueError, match="base_ref"):
        fetch_reviewgate_yml_and_config_hash(
            _TOKEN,
            owner="o",
            repo="r",
            base_ref="   ",
        )
