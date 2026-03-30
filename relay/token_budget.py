"""Token budget management for relay LLM calls.

Uses tiktoken to count tokens and truncate message histories to fit within
model context windows, preventing output truncation and context overflow.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import tiktoken

logger = logging.getLogger(__name__)

# Context window sizes for known Azure OpenAI models (input tokens).
# Values are conservative estimates leaving headroom for internal overhead.
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

_DEFAULT_CONTEXT_WINDOW = 128_000

# Placeholder inserted when old assistant code messages are compressed.
_CODE_PLACEHOLDER = "[이전 전략 코드 — 워크스페이스에 반영됨]"


@lru_cache(maxsize=8)
def _get_encoding(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    enc = _get_encoding(model)
    return len(enc.encode(text))


def get_context_window(model: str) -> int:
    for prefix, size in _MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return size
    return _DEFAULT_CONTEXT_WINDOW


def fit_messages(
    system_prompt: str,
    messages: list[dict[str, str]],
    model: str = "gpt-4o",
    max_output_tokens: int = 16_384,
) -> list[dict[str, str]]:
    """Truncate messages to fit within the model's context window.

    Strategy:
    1. Reserve tokens for system prompt + max_output_tokens + safety margin.
    2. Always keep the last message (user's current request).
    3. Keep the most recent assistant message with code intact.
    4. Replace older assistant code messages with a placeholder.
    5. If still over budget, drop oldest messages first.
    """
    context_window = get_context_window(model)
    safety_margin = 1024
    system_tokens = count_tokens(system_prompt, model)
    budget = context_window - system_tokens - max_output_tokens - safety_margin

    if budget <= 0:
        logger.warning(
            "System prompt (%d tokens) + max_output (%d) exceeds context window (%d)",
            system_tokens,
            max_output_tokens,
            context_window,
        )
        budget = 4096  # fallback: at least keep some messages

    if not messages:
        return messages

    # Phase 1: Compress old assistant code messages (keep only the latest one)
    compressed = list(messages)
    last_code_idx = -1
    for i in range(len(compressed) - 1, -1, -1):
        msg = compressed[i]
        if msg.get("role") == "assistant" and _looks_like_code(msg.get("content", "")):
            last_code_idx = i
            break

    for i, msg in enumerate(compressed):
        if (
            i != last_code_idx
            and msg.get("role") == "assistant"
            and _looks_like_code(msg.get("content", ""))
        ):
            compressed[i] = {"role": "assistant", "content": _CODE_PLACEHOLDER}

    # Phase 2: Check total token count
    total = sum(count_tokens(m.get("content", ""), model) for m in compressed)
    if total <= budget:
        return compressed

    # Phase 3: Drop oldest messages (but always keep the last message)
    while len(compressed) > 1 and total > budget:
        dropped = compressed.pop(0)
        total -= count_tokens(dropped.get("content", ""), model)

    if total > budget:
        logger.warning(
            "Single message (%d tokens) exceeds budget (%d); sending anyway",
            total,
            budget,
        )

    return compressed


def _looks_like_code(content: str) -> bool:
    """Heuristic: content looks like Python strategy code."""
    if not content or len(content) < 100:
        return False
    indicators = ["class ", "def on_bar", "def initialize", "ctx.", "import "]
    return sum(1 for ind in indicators if ind in content) >= 2
