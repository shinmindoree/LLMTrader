"""BB + RSI + OI mean-reversion LONG/SHORT strategy for BTCUSDT-PERP 15m.

Discovered by the alpha-lab `_alpha_lab/pass4_multifamily.py` sweep + the focused
OI-only sub-search in `_alpha_lab/subsearch_oi_only.py`.

Spec (15m):
  - Entry LONG:
        close <= BB_lower(period=20, std=2.0)
        AND RSI(14) <= 35
        AND OI 96h pct_change <= +2.0%   (fade overheated longs blowoff)
  - Entry SHORT:
        close >= BB_upper(period=20, std=2.0)
        AND RSI(14) >= 70
        AND OI 96h pct_change >= -6.0%   (avoid shorting into capitulation)
  - Exit:    TP +2.0%, SL -1.0%, max_hold 32 bars (8h)
  - Edge-triggered: enter only on the rising edge of the entry condition.
  - Conservative SL fills (matches the vectorized lab):
        LONG  SL: open<=sl→fill at open;        elif low<=sl  →fill at sl_level
        SHORT SL: open>=sl→fill at open;        elif high>=sl →fill at sl_level
        TP fills exactly at the TP level (limit-style).

Validation on the production engine (commission=4bp, slippage=1bp,
parquet data BTCUSDT_15m_klines.parquet):
  Full 2023-04..2026-04:  +335% / 869 trades  (compound 100% notional)
  OOS  2025-05..2026-04:   +99% / 471 trades / 1.30 t/day
  Sub-window May–Aug 2025:  +46% / 151 trades / 47 SL / 40 TP / 63 TIME
  (vs the additive-equity vectorized lab on the same window: +47% / 159 trades)

Live integration:
  - Reuses the same OI provider as oi_capitulation_bottom_strategy.
    Backtest: parquet at data/perp_meta/BTCUSDT_oi_5m.parquet.
    Live:     Redis ZSET `oi:BTCUSDT:hist` populated by scripts/oi_ingestor.py.
  - BB and RSI computed via the runner's TA-Lib indicator registration.
  - First bar of trading requires ≥96h of OI history. The OI provider keeps
    the full 5m parquet in memory in backtest, and reads from the Redis ZSET
    in live (sized by oi_ingestor's retention).
"""
from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from strategy.base import Strategy
from strategy.context import StrategyContext

try:
    from indicators.oi_provider import get_oi_provider
except Exception as _exc:  # noqa: BLE001
    get_oi_provider = None  # type: ignore[assignment]
    _OI_IMPORT_ERR: Exception | None = _exc
else:
    _OI_IMPORT_ERR = None


# ---------------------------------------------------------------------------
# TA-Lib indicator registration helper (matches the shared pattern across
# scripts/strategies/*).
# ---------------------------------------------------------------------------
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
    try:
        import numpy as np
        abstract = importlib.import_module("talib.abstract")
    except Exception:  # noqa: BLE001
        return

    _OHLCV_KEYS = {"open", "high", "low", "close", "volume", "real"}

    def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> Any:
        output = kwargs.pop("output", None)
        output_index = kwargs.pop("output_index", None)
        price_source = kwargs.pop("price", None)

        if args:
            if len(args) == 1 and "period" not in kwargs and "timeperiod" not in kwargs:
                kwargs["period"] = args[0]
            else:
                raise TypeError("indicator params must be passed as keywords or a single period")

        if "period" in kwargs and "timeperiod" not in kwargs:
            kwargs["timeperiod"] = kwargs.pop("period")

        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw_inputs = inputs()
        prepared_inputs = {
            key: (np.asarray(list(values), dtype="float64")
                  if not hasattr(values, "dtype") else values)
            for key, values in raw_inputs.items()
        }
        if "real" not in prepared_inputs and "close" in prepared_inputs:
            prepared_inputs["real"] = prepared_inputs["close"]

        if price_source is not None and price_source.lower() in _OHLCV_KEYS:
            prepared_inputs["real"] = prepared_inputs.get(
                price_source.lower(), prepared_inputs.get("close"))

        fn = abstract.Function(name.strip().upper())
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
            values_list: list[float] = []
            for series in series_list:
                v = _last_non_nan(series)
                values_list.append(float(v) if v is not None else math.nan)
            names = [f"output_{i}" for i in range(len(values_list))]
            if output_index is not None:
                idx = int(output_index)
                return values_list[idx] if 0 <= idx < len(values_list) else math.nan
            return {names[i]: values_list[i] for i in range(len(values_list))}

        v = _last_non_nan(result)
        return float(v) if v is not None else math.nan

    ctx.register_indicator(name, _indicator)


# ---------------------------------------------------------------------------
# Strategy params (winning config from sub-search)
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = {
    "bb_period": 20,
    "bb_stddev": 2.0,
    "rsi_period": 14,
    "rsi_long_level": 35.0,
    "rsi_short_level": 70.0,
    # OI 96h (= 384 bars on 15m) lookback
    "oi_lookback_ms": 96 * 3600 * 1000,
    "oi_max_for_long": 0.020,    # don't long when 96h OI is up >2%
    "oi_min_for_short": -0.060,  # don't short when 96h OI is down >6% (capitulation)
    "tp_pct": 0.020,
    "sl_pct": 0.010,
    "max_hold_bars": 32,         # 8h on 15m bars
    "entry_pct": None,
}


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {"name": "bb_period", "type": "int", "min": 5, "max": 200, "label": "BB period"},
    {"name": "bb_stddev", "type": "float", "min": 0.5, "max": 4.0, "step": 0.1,
     "label": "BB std dev"},
    {"name": "rsi_period", "type": "int", "min": 2, "max": 50, "label": "RSI period"},
    {"name": "rsi_long_level", "type": "float", "min": 5.0, "max": 50.0, "step": 1.0,
     "label": "RSI long entry"},
    {"name": "rsi_short_level", "type": "float", "min": 50.0, "max": 95.0, "step": 1.0,
     "label": "RSI short entry"},
    {"name": "oi_lookback_ms", "type": "int", "min": 60_000,
     "max": 14 * 24 * 3600_000, "label": "OI lookback (ms)"},
    {"name": "oi_max_for_long", "type": "float", "min": -0.10, "max": 0.20,
     "step": 0.005, "label": "Max OI Δ for long"},
    {"name": "oi_min_for_short", "type": "float", "min": -0.20, "max": 0.10,
     "step": 0.005, "label": "Min OI Δ for short"},
    {"name": "tp_pct", "type": "float", "min": 0.001, "max": 0.10, "step": 0.001,
     "label": "Take profit %"},
    {"name": "sl_pct", "type": "float", "min": 0.001, "max": 0.10, "step": 0.001,
     "label": "Stop loss %"},
    {"name": "max_hold_bars", "type": "int", "min": 1, "max": 1000,
     "label": "Max hold (bars)"},
]


class BbRsiOiMeanRevStrategy(Strategy):
    """BB + RSI + OI mean-reversion LONG/SHORT strategy (15m)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.bb_period = int(p["bb_period"])
        self.bb_stddev = float(p["bb_stddev"])
        self.rsi_period = int(p["rsi_period"])
        self.rsi_long_level = float(p["rsi_long_level"])
        self.rsi_short_level = float(p["rsi_short_level"])
        self.oi_lookback_ms = int(p["oi_lookback_ms"])
        self.oi_max_for_long = float(p["oi_max_for_long"])
        self.oi_min_for_short = float(p["oi_min_for_short"])
        self.tp_pct = float(p["tp_pct"])
        self.sl_pct = float(p["sl_pct"])
        self.max_hold_bars = int(p["max_hold_bars"])
        self.entry_pct = p["entry_pct"]

        self._oi_provider: Any = None
        self._mode: str | None = None
        self._entry_bar_index: int | None = None
        self._entry_price: float | None = None
        self._bar_index: int = 0
        self._is_closing: bool = False
        self._prev_long_signal: bool = False
        self._prev_short_signal: bool = False
        self._eval_log_every_bars: int = 1
        self._last_eval_log_bar: int = -10**9

        self.params = dict(p)
        self.indicator_config = {
            "BBANDS": {"period": self.bb_period,
                        "nbdevup": self.bb_stddev, "nbdevdn": self.bb_stddev},
            "RSI": {"period": self.rsi_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        if get_oi_provider is None:
            raise RuntimeError(
                f"OI provider not importable: {_OI_IMPORT_ERR}. "
                "Verify src/indicators/oi_provider.py is on PYTHONPATH."
            )
        register_talib_indicator_all_outputs(ctx, "BBANDS")
        register_talib_indicator_all_outputs(ctx, "RSI")

        symbol = getattr(ctx, "symbol", "BTCUSDT")
        ctx_cls = type(ctx).__name__
        ctx_module = type(ctx).__module__
        if "Backtest" in ctx_cls:
            mode = "backtest"
        elif (
            "Live" in ctx_cls
            or ctx_cls == "StreamBoundStrategyContext"
            or ctx_module.startswith("live.")
        ):
            mode = "live"
        else:
            mode = None
        self._oi_provider = get_oi_provider(symbol, mode=mode)
        self._mode = mode

        self._entry_bar_index = None
        self._entry_price = None
        self._bar_index = 0
        self._is_closing = False
        self._prev_long_signal = False
        self._prev_short_signal = False
        self._last_eval_log_bar = -10**9

        # Register live OI helper indicator for the dashboard (best-effort).
        def _oi_pct_change(_inner_ctx: Any) -> float:
            ts = _bar_timestamp_from_ctx(ctx)
            if ts <= 0:
                return float("nan")
            return float(self._oi_provider.pct_change(ts, lookback_ms=self.oi_lookback_ms))

        try:
            ctx.register_indicator("oi_pct_change", _oi_pct_change)
        except Exception:  # noqa: BLE001
            pass

        self._emit_event(ctx, "BB_OI_INIT", {
            "symbol": symbol, "mode": mode,
            "bb_period": self.bb_period, "bb_stddev": self.bb_stddev,
            "rsi_period": self.rsi_period,
            "rsi_long_level": self.rsi_long_level,
            "rsi_short_level": self.rsi_short_level,
            "oi_lookback_h": self.oi_lookback_ms / 3600_000,
            "oi_max_for_long": self.oi_max_for_long,
            "oi_min_for_short": self.oi_min_for_short,
            "tp_pct": self.tp_pct, "sl_pct": self.sl_pct,
            "max_hold_bars": self.max_hold_bars,
        })

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self._is_closing = False
            self._entry_bar_index = None
            self._entry_price = None

        is_new_bar = bool(bar.get("is_new_bar", True))
        # Mirror the vectorized backtester: exits are evaluated bar-by-bar
        # against the bar's full OHLC at the close tick (is_new_bar=True).
        # The engine still emits intra-bar OPEN/LOW ticks for risk-manager
        # bookkeeping, but we ignore them here so SL/TP fire at the same
        # logical bar boundary as the sweep.
        if not is_new_bar:
            return

        close = float(bar.get("close", bar.get("price", 0.0)) or 0.0)
        if not math.isfinite(close) or close <= 0:
            return
        open_ = float(bar.get("open", close) or close)
        high = float(bar.get("high", close) or close)
        low = float(bar.get("low", close) or close)

        # ---- Exit checks against this bar's OHLC (vectorized semantics) ----
        if ctx.position_size != 0 and self._entry_price is not None and not self._is_closing:
            if ctx.position_size > 0:
                tp_level = self._entry_price * (1.0 + self.tp_pct)
                sl_level = self._entry_price * (1.0 - self.sl_pct)
                # Pessimistic SL: gap-down at open OR touch during bar.
                if open_ <= sl_level:
                    self._is_closing = True
                    sl_fill = open_
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            sl_fill, reason=f"BB+OI: SL_GAP -{self.sl_pct * 100:.1f}%"
                        )
                    else:
                        ctx.close_position(reason=f"BB+OI: SL_GAP -{self.sl_pct * 100:.1f}%")
                    self._emit_event(ctx, "BB_OI_EXIT_SL_LONG", {
                        "entry_price": self._entry_price,
                        "exit_price": sl_fill, "sl_level": sl_level,
                        "kind": "GAP",
                    })
                    return
                if low <= sl_level:
                    self._is_closing = True
                    sl_fill = sl_level
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            sl_fill, reason=f"BB+OI: SL -{self.sl_pct * 100:.1f}%"
                        )
                    else:
                        ctx.close_position(reason=f"BB+OI: SL -{self.sl_pct * 100:.1f}%")
                    self._emit_event(ctx, "BB_OI_EXIT_SL_LONG", {
                        "entry_price": self._entry_price,
                        "exit_price": sl_fill, "sl_level": sl_level,
                        "kind": "TOUCH",
                    })
                    return
                if high >= tp_level:
                    self._is_closing = True
                    tp_fill = tp_level
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            tp_fill, reason=f"BB+OI: TP +{self.tp_pct * 100:.1f}%"
                        )
                    else:
                        ctx.close_position(reason=f"BB+OI: TP +{self.tp_pct * 100:.1f}%")
                    self._emit_event(ctx, "BB_OI_EXIT_TP_LONG", {
                        "entry_price": self._entry_price,
                        "exit_price": tp_fill, "tp_level": tp_level,
                    })
                    return
            else:  # SHORT
                tp_level = self._entry_price * (1.0 - self.tp_pct)
                sl_level = self._entry_price * (1.0 + self.sl_pct)
                if open_ >= sl_level:
                    self._is_closing = True
                    sl_fill = open_
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            sl_fill, reason=f"BB+OI: SL_GAP +{self.sl_pct * 100:.1f}%"
                        )
                    else:
                        ctx.close_position(reason=f"BB+OI: SL_GAP +{self.sl_pct * 100:.1f}%")
                    self._emit_event(ctx, "BB_OI_EXIT_SL_SHORT", {
                        "entry_price": self._entry_price,
                        "exit_price": sl_fill, "sl_level": sl_level,
                        "kind": "GAP",
                    })
                    return
                if high >= sl_level:
                    self._is_closing = True
                    sl_fill = sl_level
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            sl_fill, reason=f"BB+OI: SL +{self.sl_pct * 100:.1f}%"
                        )
                    else:
                        ctx.close_position(reason=f"BB+OI: SL +{self.sl_pct * 100:.1f}%")
                    self._emit_event(ctx, "BB_OI_EXIT_SL_SHORT", {
                        "entry_price": self._entry_price,
                        "exit_price": sl_fill, "sl_level": sl_level,
                        "kind": "TOUCH",
                    })
                    return
                if low <= tp_level:
                    self._is_closing = True
                    tp_fill = tp_level
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            tp_fill, reason=f"BB+OI: TP +{self.tp_pct * 100:.1f}%"
                        )
                    else:
                        ctx.close_position(reason=f"BB+OI: TP +{self.tp_pct * 100:.1f}%")
                    self._emit_event(ctx, "BB_OI_EXIT_TP_SHORT", {
                        "entry_price": self._entry_price,
                        "exit_price": tp_fill, "tp_level": tp_level,
                    })
                    return
            # Time-based exit: close at this bar's close.
            if (self._entry_bar_index is not None and
                    self._bar_index - self._entry_bar_index >= self.max_hold_bars):
                self._is_closing = True
                ctx.close_position(reason=f"BB+OI: time exit ({self.max_hold_bars} bars)")
                self._emit_event(ctx, "BB_OI_EXIT_TIME", {
                    "entry_price": self._entry_price, "exit_price": close,
                    "held_bars": self._bar_index - self._entry_bar_index,
                })
                return

        # ---- Entry: only on a new-bar close.
        try:
            open_orders = ctx.get_open_orders() or []
        except Exception:  # noqa: BLE001
            open_orders = []
        if open_orders:
            return

        self._bar_index += 1
        if ctx.position_size != 0:
            return

        # BB and RSI come from the runner's incremental TA-Lib pipeline.
        bb = ctx.get_indicator(
            "BBANDS", period=self.bb_period,
            nbdevup=self.bb_stddev, nbdevdn=self.bb_stddev,
        )
        if not isinstance(bb, dict):
            return
        upper = float(bb.get("upperband", bb.get("output_0", math.nan)))
        lower = float(bb.get("lowerband", bb.get("output_2", math.nan)))
        rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        if not all(math.isfinite(v) for v in (upper, lower, rsi)):
            return

        # OI 96h pct change (provider returns NaN until enough history).
        ts = _bar_timestamp_from_bar(bar)
        if ts <= 0:
            return
        try:
            oi_chg = float(self._oi_provider.pct_change(ts, lookback_ms=self.oi_lookback_ms))
        except Exception:  # noqa: BLE001
            return
        if not math.isfinite(oi_chg):
            return

        long_signal = (close <= lower
                       and rsi <= self.rsi_long_level
                       and oi_chg <= self.oi_max_for_long)
        short_signal = (close >= upper
                        and rsi >= self.rsi_short_level
                        and oi_chg >= self.oi_min_for_short)

        if (self._bar_index - self._last_eval_log_bar) >= max(1, self._eval_log_every_bars):
            self._emit_event(ctx, "BB_OI_SIGNAL_EVAL", {
                "bar_ts": ts, "close": close,
                "bb_upper": upper, "bb_lower": lower,
                "rsi": rsi,
                "oi_pct_96h": round(oi_chg * 100.0, 4),
                "long_signal": long_signal, "short_signal": short_signal,
                "long_edge": long_signal and not self._prev_long_signal,
                "short_edge": short_signal and not self._prev_short_signal,
            })
            self._last_eval_log_bar = self._bar_index

        # Edge-triggered entries (one trade per signal cluster, matches the sweep).
        if long_signal and not self._prev_long_signal:
            reason = (f"BB+RSI+OI long: c={close:.2f}<=L={lower:.2f}, "
                      f"RSI={rsi:.1f}<={self.rsi_long_level:.0f}, "
                      f"OI96h={oi_chg * 100:.2f}%")
            if self.entry_pct is None:
                ctx.enter_long(reason=reason)
            else:
                ctx.enter_long(reason=reason, entry_pct=float(self.entry_pct))
            self._entry_bar_index = self._bar_index
            self._entry_price = close
            self._emit_event(ctx, "BB_OI_ENTRY_LONG", {
                "bar_ts": ts, "entry_price": close,
                "bb_lower": lower, "rsi": rsi,
                "oi_pct_96h": round(oi_chg * 100.0, 4),
                "tp_level": round(close * (1.0 + self.tp_pct), 4),
                "sl_level": round(close * (1.0 - self.sl_pct), 4),
            })
        elif short_signal and not self._prev_short_signal:
            reason = (f"BB+RSI+OI short: c={close:.2f}>=U={upper:.2f}, "
                      f"RSI={rsi:.1f}>={self.rsi_short_level:.0f}, "
                      f"OI96h={oi_chg * 100:.2f}%")
            if self.entry_pct is None:
                ctx.enter_short(reason=reason)
            else:
                ctx.enter_short(reason=reason, entry_pct=float(self.entry_pct))
            self._entry_bar_index = self._bar_index
            self._entry_price = close
            self._emit_event(ctx, "BB_OI_ENTRY_SHORT", {
                "bar_ts": ts, "entry_price": close,
                "bb_upper": upper, "rsi": rsi,
                "oi_pct_96h": round(oi_chg * 100.0, 4),
                "tp_level": round(close * (1.0 - self.tp_pct), 4),
                "sl_level": round(close * (1.0 + self.sl_pct), 4),
            })

        self._prev_long_signal = long_signal
        self._prev_short_signal = short_signal

    # ---- helpers -----------------------------------------------------------
    def _emit_event(self, ctx: Any, action: str, data: dict[str, Any]) -> None:
        fn = getattr(ctx, "log_event", None)
        if not callable(fn):
            return
        try:
            fn(action, data)
        except Exception:  # noqa: BLE001
            pass


def _bar_timestamp_from_bar(bar: dict[str, Any]) -> int:
    for key in ("bar_timestamp", "timestamp"):
        try:
            v = int(bar.get(key, 0) or 0)
        except Exception:  # noqa: BLE001
            v = 0
        if v > 0:
            return v
    return 0


def _bar_timestamp_from_ctx(ctx: StrategyContext) -> int:
    for attr in ("_current_timestamp", "current_timestamp"):
        v = getattr(ctx, attr, None)
        if v:
            try:
                return int(v)
            except Exception:  # noqa: BLE001
                pass
    return 0
