"""MACD histogram 부호 기반 즉시 진입 + 수익률 청산 전략.

요구사항:
- 포지션이 0이면 즉시 진입
  - MACD histogram < 0  → 숏 진입
  - MACD histogram > 0  → 롱 진입
- 포지션 청산
  - 수익률이 0.12% (0.0012) 달성 시 즉시 청산
  - StopLoss는 시스템 설정대로(컨텍스트/리스크 매니저) 처리
- 모든 주문은 시장가(enter_long/enter_short/close_position 사용)

참고:
- `indicator_strategy_template.py`의 구조(guard → 지표조회 → 신호판단 → 상태업데이트)를 따른다.
- 라이브에서 중복 주문 방지: 미체결 주문이 있으면 신호 무시(`ctx.get_open_orders()` 가드).
"""

from __future__ import annotations

import importlib
import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


def _last_non_nan(values: Any) -> float | None:
    try:
        n = int(getattr(values, "size", len(values)))
    except Exception:  # noqa: BLE001
        return None
    for i in range(n - 1, -1, -1):
        try:
            v = float(values[i])
        except Exception:  # noqa: BLE001
            continue
        if not math.isnan(v):
            return v
    return None


def register_talib_indicator_all_outputs(ctx: StrategyContext, name: str) -> None:
    """TA-Lib builtin 인디케이터를 "가능하면 모든 output을 dict로" 반환하도록 오버라이드한다.

    - single-output: float
    - multi-output: dict[str, float]
    """
    try:
        import numpy as np  # type: ignore

        abstract = importlib.import_module("talib.abstract")
    except Exception:  # noqa: BLE001
        return

    def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> Any:
        output = kwargs.pop("output", None)
        output_index = kwargs.pop("output_index", None)

        if args:
            if len(args) == 1 and "period" not in kwargs and "timeperiod" not in kwargs:
                kwargs["period"] = args[0]
            else:
                raise TypeError("builtin indicator params must be passed as keywords (or single period)")

        if "period" in kwargs and "timeperiod" not in kwargs:
            kwargs["timeperiod"] = kwargs.pop("period")

        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw_inputs = inputs()
        prepared_inputs = {
            key: (np.asarray(list(values), dtype="float64") if not hasattr(values, "dtype") else values)
            for key, values in raw_inputs.items()
        }
        if "real" not in prepared_inputs and "close" in prepared_inputs:
            prepared_inputs["real"] = prepared_inputs["close"]

        normalized = name.strip().upper()
        fn = abstract.Function(normalized)
        result = fn(prepared_inputs, **kwargs)

        if isinstance(result, dict):
            out: dict[str, float] = {}
            for key, series in result.items():
                v = _last_non_nan(series)
                out[str(key)] = float(v) if v is not None else math.nan
            if output is not None:
                return float(out.get(str(output), math.nan))
            if output_index is not None:
                keys = list(out.keys())
                idx = int(output_index)
                return float(out.get(keys[idx], math.nan)) if 0 <= idx < len(keys) else math.nan
            return out

        if isinstance(result, (list, tuple)):
            series_list = list(result)
            values: list[float] = []
            for series in series_list:
                v = _last_non_nan(series)
                values.append(float(v) if v is not None else math.nan)
            names = (
                ["macd", "macdsignal", "macdhist"][: len(values)]
                if normalized == "MACD"
                else [f"output_{i}" for i in range(len(values))]
            )
            if output is not None:
                try:
                    return values[names.index(str(output))]
                except ValueError:
                    return math.nan
            if output_index is not None:
                idx = int(output_index)
                return values[idx] if 0 <= idx < len(values) else math.nan
            return {names[i]: values[i] for i in range(len(values))}

        v = _last_non_nan(result)
        return float(v) if v is not None else math.nan

    ctx.register_indicator(name, _indicator)


def _get_macd_hist(ctx: StrategyContext, *, fast: int, slow: int, signal: int) -> float:
    macd = ctx.get_indicator(
        "MACD",
        fastperiod=int(fast),
        slowperiod=int(slow),
        signalperiod=int(signal),
    )
    if isinstance(macd, dict):
        hist = macd.get("macdhist", macd.get("MACDHIST", float("nan")))
        try:
            return float(hist)
        except Exception:  # noqa: BLE001
            return float("nan")
    # 일부 환경에서는 MACD가 float(첫 output)만 반환될 수 있으므로 histogram을 output_index로 조회.
    try:
        return float(
            ctx.get_indicator(
                "MACD",
                fastperiod=int(fast),
                slowperiod=int(slow),
                signalperiod=int(signal),
                output_index=2,
            )
        )
    except Exception:  # noqa: BLE001
        return float("nan")


class MacdHistImmediateEntryTakeProfitStrategy(Strategy):
    """MACD histogram 부호 기반 즉시 진입 + 0.12% 익절 청산 전략."""

    # LiveTradingEngine는 기본적으로 "새 봉(is_new_bar=True)"에서만 on_bar를 호출한다.
    # 즉시 진입을 원하면 tick에서도 on_bar를 호출하도록 활성화해야 한다.
    run_on_tick = True

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        take_profit_pct: float = 0.0012,
    ) -> None:
        super().__init__()
        if fast_period <= 0:
            raise ValueError("fast_period must be > 0")
        if slow_period <= 0:
            raise ValueError("slow_period must be > 0")
        if signal_period <= 0:
            raise ValueError("signal_period must be > 0")
        if fast_period >= slow_period:
            raise ValueError("fast_period must be < slow_period")
        if take_profit_pct <= 0:
            raise ValueError("take_profit_pct must be > 0")

        self.fast_period = int(fast_period)
        self.slow_period = int(slow_period)
        self.signal_period = int(signal_period)
        self.take_profit_pct = float(take_profit_pct)

        self.is_closing: bool = False  # 청산 주문 진행 중 플래그(중복 청산 방지)

        self.params = {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "signal_period": self.signal_period,
            "take_profit_pct": self.take_profit_pct,
        }
        self.indicator_config = {
            "MACD": {
                "fastperiod": self.fast_period,
                "slowperiod": self.slow_period,
                "signalperiod": self.signal_period,
            },
        }

    def initialize(self, ctx: StrategyContext) -> None:
        # multi-output 인디케이터(MACD 등)도 "전체 output dict"로 반환되도록 표준화.
        register_talib_indicator_all_outputs(ctx, "MACD")
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # ===== 청산 플래그 리셋 =====
        if ctx.position_size == 0:
            self.is_closing = False

        # ===== 미체결 주문 가드(라이브 전용) =====
        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        # ===== 익절 청산(즉시) =====
        if ctx.position_size != 0 and not self.is_closing:
            entry = float(ctx.position_entry_price)
            position_size = float(ctx.position_size)
            unrealized_pnl = float(ctx.unrealized_pnl)
            position_notional = abs(position_size) * entry

            if entry > 0 and position_notional > 0 and math.isfinite(unrealized_pnl):
                pnl_pct = unrealized_pnl / position_notional
                if math.isfinite(pnl_pct) and pnl_pct >= self.take_profit_pct:
                    self.is_closing = True
                    ctx.close_position(
                        reason=f"TakeProfit {pnl_pct * 100:.3f}% (target {self.take_profit_pct * 100:.3f}%)",
                        use_chase=False,
                    )
                    return

        # ===== 포지션 0이면 즉시 진입 =====
        if ctx.position_size != 0:
            return

        hist = _get_macd_hist(ctx, fast=self.fast_period, slow=self.slow_period, signal=self.signal_period)
        if not math.isfinite(hist) or hist == 0:
            return

        if hist > 0:
            qty = float(ctx.calc_entry_quantity())
            if qty > 0:
                ctx.buy(qty, price=None, reason=f"MACD hist>0 ({hist:.6f})", use_chase=False)
            return

        qty = float(ctx.calc_entry_quantity())
        if qty > 0:
            ctx.sell(qty, price=None, reason=f"MACD hist<0 ({hist:.6f})", use_chase=False)
