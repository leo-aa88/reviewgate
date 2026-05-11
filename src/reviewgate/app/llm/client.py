"""Hosted LLM client with structured JSON fallback (``docs/DESIGN.md`` §11.3; issue #57)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Final

from pydantic import ValidationError

from reviewgate.app.llm.schemas import LlmReviewabilityReport
from reviewgate.app.settings import AppSettings

logger = logging.getLogger(__name__)

_REPAIR_SYSTEM: Final[str] = (
    "You output only valid JSON. Fix the following text so it is a single JSON "
    "object with keys reviewability, summary, issues, suggested_labels, "
    "split_suggestions, reviewer_checklist. No markdown."
)


@dataclass(frozen=True, slots=True)
class LlmCallUsage:
    """Token usage and provider slug for persistence (issue #63)."""

    input_tokens: int
    output_tokens: int
    provider: str


def _combine_llm_usage(
    first: LlmCallUsage | None,
    second: LlmCallUsage | None,
) -> LlmCallUsage | None:
    """Sum token counts across a primary call plus an optional repair call."""

    if first is None and second is None:
        return None
    if first is None:
        return second
    if second is None:
        return first
    provider = second.provider or first.provider
    return LlmCallUsage(
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        provider=provider,
    )


@dataclass(frozen=True, slots=True)
class LlmCallResult:
    """Outcome of :func:`complete_reviewability_json`."""

    parsed: LlmReviewabilityReport | None
    usage: LlmCallUsage | None


def _extract_message_content(data: object) -> str | None:
    if not isinstance(data, dict):
        return None
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def _openai_chat_completion(
    settings: AppSettings,
    *,
    messages: list[dict[str, str]],
) -> tuple[object | None, LlmCallUsage | None]:
    """Perform one chat completion via the OpenAI HTTP API (no SDK required)."""

    import httpx

    key = settings.openai_api_key
    if key is None or not key.get_secret_value().strip():
        return None, None
    headers = {
        "Authorization": f"Bearer {key.get_secret_value().strip()}",
        "Content-Type": "application/json",
    }
    body: dict[str, object] = {
        "model": settings.llm_model.strip(),
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    url = f"{settings.openai_api_base_url.rstrip('/')}/v1/chat/completions"
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload: object = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("llm_openai_request_failed", exc_info=exc)
        return None, None

    if not isinstance(payload, dict):
        return None, None
    usage_raw = payload.get("usage")
    in_tok = 0
    out_tok = 0
    if isinstance(usage_raw, dict):
        raw_in = usage_raw.get("prompt_tokens")
        raw_out = usage_raw.get("completion_tokens")
        if isinstance(raw_in, int) and raw_in >= 0:
            in_tok = raw_in
        if isinstance(raw_out, int) and raw_out >= 0:
            out_tok = raw_out
    content = _extract_message_content(payload)
    if content is None:
        return None, None
    usage = LlmCallUsage(
        input_tokens=in_tok,
        output_tokens=out_tok,
        provider="openai",
    )
    return content, usage


def _parse_llm_json(text: str) -> LlmReviewabilityReport | None:
    stripped = text.strip()
    try:
        obj: object = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        return LlmReviewabilityReport.model_validate(obj)
    except ValidationError:
        return None


def complete_reviewability_json(
    settings: AppSettings,
    *,
    system_prompt: str,
    user_prompt: str,
) -> LlmCallResult:
    """§11.3 ordered path: structured JSON → parse → one repair → failure.

    Uses the OpenAI-compatible ``/v1/chat/completions`` endpoint configured via
    :class:`~reviewgate.app.settings.AppSettings`.

    Args:
        settings: Process settings (API key, model, base URL).
        system_prompt: Static instructions (§11.6 asset).
        user_prompt: Serialized PR/report payload (§11.5).

    Returns:
        Parsed report when any step succeeds; otherwise ``parsed`` is ``None``
        and the caller should use deterministic-only output.
    """

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    content, usage = _openai_chat_completion(settings, messages=messages)
    if isinstance(content, str):
        parsed = _parse_llm_json(content)
        if parsed is not None:
            return LlmCallResult(parsed=parsed, usage=usage)

    if isinstance(content, str) and content.strip():
        repair_messages: list[dict[str, str]] = [
            {"role": "system", "content": _REPAIR_SYSTEM},
            {
                "role": "user",
                "content": content.strip()[:12_000],
            },
        ]
        fixed, repair_usage = _openai_chat_completion(
            settings,
            messages=repair_messages,
        )
        if isinstance(fixed, str):
            parsed = _parse_llm_json(fixed)
            if parsed is not None:
                return LlmCallResult(
                    parsed=parsed,
                    usage=_combine_llm_usage(usage, repair_usage),
                )

    return LlmCallResult(parsed=None, usage=usage)
