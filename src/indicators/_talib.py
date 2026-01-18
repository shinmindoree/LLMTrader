"""Deprecated shim (legacy).

TA-Lib 기반 builtin 인디케이터는 `indicators.builtin`을 사용합니다.
"""

from __future__ import annotations

from typing import Any


def indicator_backend() -> str:
    return "talib"


def talib_rsi_from_closes(closes: list[float], period: int) -> float | None:  # noqa: ARG001
    raise RuntimeError("Deprecated: use indicators.builtin.compute('RSI', ...) instead.")


def talib_ema_from_closes(closes: list[float], period: int) -> float | None:  # noqa: ARG001
    raise RuntimeError("Deprecated: use indicators.builtin.compute('EMA', ...) instead.")


def talib_sma_from_closes(closes: list[float], period: int) -> float | None:  # noqa: ARG001
    raise RuntimeError("Deprecated: use indicators.builtin.compute('SMA', ...) instead.")
