"""Dynamic token budget manager for prompt optimization.

Pre-processes prompts destined for cloud LLM APIs (Azure OpenAI, etc.)
by computing exact token counts and dynamically truncating chart data
to fit within the model's context window.

Truncation priority (highest → lowest preservation):
  1. System prompt — always preserved in full.
  2. User natural-language input — always preserved in full.
  3. Chart data (JSON array) — oldest entries trimmed first via array slicing.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

import tiktoken

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

# Mapping of known model families to their context window sizes.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_048_576,
    "gpt-4.1-mini": 1_048_576,
    "gpt-4.1-nano": 1_048_576,
    "o3-mini": 200_000,
    "o3": 200_000,
    "o4-mini": 200_000,
}


@lru_cache(maxsize=8)
def _get_encoding(model: str) -> tiktoken.Encoding:
    """Get the tiktoken encoding for a model, with fallback."""
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Count the number of tokens in a text string."""
    enc = _get_encoding(model)
    return len(enc.encode(text))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_optimized_prompt(
    system_prompt: str,
    user_input: str,
    chart_data: list[dict[str, Any]],
    *,
    max_total_tokens: int = 4000,
    model: str = "gpt-4o",
    reserved_output_tokens: int = 0,
) -> str:
    """Build a token-optimized prompt string that fits within the budget.

    Computes exact token counts for each component, then truncates the
    chart_data array from the oldest entries (front) until the total fits
    within ``max_total_tokens``.

    Args:
        system_prompt: The system instruction prompt (always preserved).
        user_input: The user's natural-language request (always preserved).
        chart_data: Historical OHLCV chart data as a list of dicts,
            ordered chronologically (oldest first).
        max_total_tokens: Hard ceiling on total prompt tokens.
        model: Model name for tokenizer selection.
        reserved_output_tokens: Tokens to reserve for the model's response.
            Subtracted from max_total_tokens before fitting.

    Returns:
        A single optimized prompt string ready to send to the LLM API.

    Raises:
        ValueError: If system_prompt + user_input alone exceed the budget.
    """
    effective_budget = max_total_tokens - reserved_output_tokens
    if effective_budget <= 0:
        raise ValueError(
            f"reserved_output_tokens ({reserved_output_tokens}) "
            f">= max_total_tokens ({max_total_tokens})"
        )

    # Token counts for fixed components
    system_tokens = count_tokens(system_prompt, model)
    user_tokens = count_tokens(user_input, model)

    # Overhead: separators, role markers, JSON wrapper, etc.
    overhead_tokens = 20  # conservative estimate for formatting

    fixed_tokens = system_tokens + user_tokens + overhead_tokens

    if fixed_tokens > effective_budget:
        raise ValueError(
            f"System prompt ({system_tokens} tokens) + user input "
            f"({user_tokens} tokens) + overhead ({overhead_tokens}) = "
            f"{fixed_tokens} tokens, which already exceeds the budget "
            f"of {effective_budget} tokens. Cannot fit any chart data."
        )

    available_for_chart = effective_budget - fixed_tokens

    # Optimize chart data to fit remaining budget
    truncated_chart = _truncate_chart_data(
        chart_data, available_for_chart, model
    )

    # Assemble final prompt
    return _assemble_prompt(system_prompt, user_input, truncated_chart)


def _truncate_chart_data(
    chart_data: list[dict[str, Any]],
    token_budget: int,
    model: str,
) -> list[dict[str, Any]]:
    """Truncate chart data array to fit within the given token budget.

    Uses binary-search–style slicing: starts from the full array, then
    trims the oldest (leftmost) entries until the JSON representation
    fits. This is O(log n) in the number of binary search steps.

    Args:
        chart_data: Full chronological chart data (oldest first).
        token_budget: Maximum tokens allowed for the chart JSON.
        model: Model name for tokenizer.

    Returns:
        A (possibly shorter) slice of chart_data, keeping the most recent
        entries.
    """
    if not chart_data:
        return []

    # Fast path: entire dataset fits
    full_json = json.dumps(chart_data, separators=(",", ":"))
    full_tokens = count_tokens(full_json, model)
    if full_tokens <= token_budget:
        logger.debug(
            "Chart data fits entirely: %d tokens <= %d budget",
            full_tokens,
            token_budget,
        )
        return chart_data

    if token_budget <= 0:
        logger.info("No token budget for chart data; omitting entirely")
        return []

    # Binary search for the optimal slice start index
    n = len(chart_data)
    lo, hi = 0, n  # lo = start index of the slice we keep

    while lo < hi:
        mid = (lo + hi) // 2
        slice_json = json.dumps(chart_data[mid:], separators=(",", ":"))
        slice_tokens = count_tokens(slice_json, model)
        if slice_tokens <= token_budget:
            hi = mid  # can potentially keep more (earlier) data
        else:
            lo = mid + 1  # must trim more from the front

    result = chart_data[lo:]
    trimmed = n - len(result)

    if result:
        result_json = json.dumps(result, separators=(",", ":"))
        result_tokens = count_tokens(result_json, model)
        logger.info(
            "Chart data truncated: kept %d/%d entries (%d tokens), "
            "trimmed %d oldest entries",
            len(result),
            n,
            result_tokens,
            trimmed,
        )
    else:
        logger.warning(
            "Chart data entirely trimmed: even a single entry exceeds "
            "the %d-token budget",
            token_budget,
        )

    return result


def _assemble_prompt(
    system_prompt: str,
    user_input: str,
    chart_data: list[dict[str, Any]],
) -> str:
    """Assemble the final prompt string from components."""
    parts: list[str] = [system_prompt, "", user_input]

    if chart_data:
        chart_json = json.dumps(chart_data, separators=(",", ":"))
        parts.append("")
        parts.append(f"[Chart Data ({len(chart_data)} candles)]")
        parts.append(chart_json)

    return "\n".join(parts)
