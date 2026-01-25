"""Indicator-only context for a single candle stream.

LiveContext는 주문/포지션/유저스트림 등 실행 기능을 포함한다.
멀티 (symbol, interval) 시그널 조합을 위해 "지표 계산만" 담당하는 경량 컨텍스트를 제공한다.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from indicators.builtin import compute as compute_builtin_indicator


class CandleStreamIndicatorContext:
    """단일 (symbol, interval) OHLCV 시계열 기반 지표 계산 컨텍스트."""

    def __init__(self, *, symbol: str, interval: str, max_len: int = 1000) -> None:
        self.symbol = symbol
        self.interval = interval
        self.max_len = int(max_len)
        self._indicator_registry: dict[str, Callable[..., Any]] = {}
        self._opens: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._closes: list[float] = []
        self._volumes: list[float] = []
        self._current_price: float = 0.0

    @property
    def current_price(self) -> float:
        return self._current_price

    def mark_price(self, price: float) -> None:
        self._current_price = float(price)

    def update_bar(
        self,
        open_price: float,
        high_price: float,
        low_price: float,
        close_price: float,
        volume: float = 0.0,
    ) -> None:
        self._opens.append(float(open_price))
        self._highs.append(float(high_price))
        self._lows.append(float(low_price))
        self._closes.append(float(close_price))
        self._volumes.append(float(volume))
        self._current_price = float(close_price)

        if self.max_len > 0 and len(self._closes) > self.max_len:
            self._opens = self._opens[-self.max_len :]
            self._highs = self._highs[-self.max_len :]
            self._lows = self._lows[-self.max_len :]
            self._closes = self._closes[-self.max_len :]
            self._volumes = self._volumes[-self.max_len :]

    def _get_builtin_indicator_inputs(self) -> dict[str, list[float]]:
        closes = list(self._closes)
        n = len(closes)
        if len(self._opens) != n or len(self._highs) != n or len(self._lows) != n or len(self._volumes) != n:
            return {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [0.0] * n,
            }
        return {
            "open": list(self._opens),
            "high": list(self._highs),
            "low": list(self._lows),
            "close": closes,
            "volume": list(self._volumes),
        }

    def register_indicator(self, name: str, func: Callable[..., Any]) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("indicator name is required")
        if not callable(func):
            raise ValueError(f"indicator '{name}' must be callable")
        self._indicator_registry[normalized.lower()] = func

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        normalized = name.strip()
        if not normalized:
            raise ValueError("indicator name is required")

        func = self._indicator_registry.get(normalized.lower())
        if func:
            return func(self, *args, **kwargs)

        if args:
            if len(args) == 1 and "period" not in kwargs and "timeperiod" not in kwargs:
                kwargs["period"] = args[0]
            else:
                raise TypeError("builtin indicator params must be passed as keywords (or single period)")

        return compute_builtin_indicator(
            normalized,
            self._get_builtin_indicator_inputs(),
            **kwargs,
        )

    def get_indicator_values(self, indicator_config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = dict(indicator_config or {})
        values: dict[str, Any] = {}
        for name, params in (config or {}).items():
            kwargs = dict(params) if isinstance(params, dict) else {}
            try:
                v = self.get_indicator(name, **kwargs)
                values[name] = v
            except Exception:  # noqa: BLE001
                values[name] = math.nan
        return values

