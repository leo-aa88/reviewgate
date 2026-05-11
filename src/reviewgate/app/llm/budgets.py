"""Token and cost budgets for hosted LLM calls (``docs/DESIGN.md`` §11.4; issue #59)."""

from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal

# Approximate list pricing for the default mini-class model (§11.4 observability).
# Operators should treat these as estimates; swap when changing models.
_DEFAULT_INPUT_USD_PER_MILLION: Final[Decimal] = Decimal("0.150")
_DEFAULT_OUTPUT_USD_PER_MILLION: Final[Decimal] = Decimal("0.600")
_HARD_MAX_USD_PER_ANALYSIS: Final[Decimal] = Decimal("0.20")

LlmInputPackaging = Literal["full", "summary_only"]


def llm_input_packaging_mode(changed_files: int) -> LlmInputPackaging:
    """Choose §11.4 packaging: large PRs use summary-only context (no patches)."""

    if changed_files > 300:
        return "summary_only"
    return "full"


def input_token_target_for_pr(changed_files: int) -> int:
    """Return §11.4 small/medium/large input token targets (capped ladder)."""

    if changed_files <= 10:
        return 4_000
    if changed_files <= 50:
        return 8_000
    return 12_000


def rough_token_estimate(text: str) -> int:
    """Conservative pre-flight token estimate without a tokenizer."""

    return max(1, len(text) // 4)


def truncate_to_token_budget(text: str, max_tokens: int) -> str:
    """Truncate UTF-8 text to an approximate token budget (best-effort)."""

    if max_tokens < 1:
        return ""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n…(truncated for token budget)"


def estimate_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    input_per_million: Decimal = _DEFAULT_INPUT_USD_PER_MILLION,
    output_per_million: Decimal = _DEFAULT_OUTPUT_USD_PER_MILLION,
) -> Decimal:
    """Estimated spend from usage counters (§11.4)."""

    in_cost = (Decimal(input_tokens) / Decimal(1_000_000)) * input_per_million
    out_cost = (Decimal(output_tokens) / Decimal(1_000_000)) * output_per_million
    return (in_cost + out_cost).quantize(Decimal("0.0001"))


def estimated_prompt_cost_within_hard_cap(
    *,
    estimated_input_tokens: int,
    assumed_output_tokens: int,
) -> bool:
    """Return ``False`` when a call would exceed the §11.4 hard cap (estimate)."""

    est = estimate_cost_usd(
        input_tokens=estimated_input_tokens,
        output_tokens=assumed_output_tokens,
    )
    return est <= _HARD_MAX_USD_PER_ANALYSIS
