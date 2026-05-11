"""Tests for LLM JSON completion and repair path (issue #57)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

pytest.importorskip("httpx")

from reviewgate.app.llm.client import _parse_llm_json, complete_reviewability_json
from reviewgate.app.settings import AppSettings


def test_parse_llm_json_accepts_valid_payload() -> None:
    raw = (
        '{"reviewability":"PASS","summary":"ok","issues":[],"suggested_labels":[],'
        '"split_suggestions":[],"reviewer_checklist":[]}'
    )
    parsed = _parse_llm_json(raw)
    assert parsed is not None
    assert parsed.reviewability == "PASS"


def test_complete_reviewability_json_sums_usage_when_repair_succeeds() -> None:
    """Malformed first response still accumulates prompt/completion tokens (issue #63)."""

    settings = AppSettings(
        openai_api_key=SecretStr("sk-test"),
        llm_model="gpt-4o-mini",
    )
    ok_json = (
        '{"reviewability":"PASS","summary":"x","issues":[],"suggested_labels":[],'
        '"split_suggestions":[],"reviewer_checklist":[]}'
    )
    bad = MagicMock()
    bad.raise_for_status = MagicMock()
    bad.json.return_value = {
        "choices": [{"message": {"content": "{not-json"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    good = MagicMock()
    good.raise_for_status = MagicMock()
    good.json.return_value = {
        "choices": [{"message": {"content": ok_json}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    mock_client = MagicMock()
    mock_client.post.side_effect = [bad, good]
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("httpx.Client", return_value=mock_cm):
        result = complete_reviewability_json(
            settings,
            system_prompt="sys",
            user_prompt="user",
        )
    assert result.parsed is not None
    assert result.usage is not None
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 22


def test_complete_reviewability_json_uses_openai_response() -> None:
    settings = AppSettings(
        openai_api_key=SecretStr("sk-test"),
        llm_model="gpt-4o-mini",
    )
    fake_json = (
        '{"reviewability":"WARN","summary":"x","issues":[],"suggested_labels":[],'
        '"split_suggestions":[],"reviewer_checklist":[]}'
    )
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": fake_json}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None

    with patch("httpx.Client", return_value=mock_cm):
        result = complete_reviewability_json(
            settings,
            system_prompt="sys",
            user_prompt="user",
        )
    assert result.parsed is not None
    assert result.parsed.reviewability == "WARN"
    assert result.usage is not None
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 20
