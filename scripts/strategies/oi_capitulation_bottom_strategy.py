"""OI Capitulation-Bottom LONG-only strategy for BTCUSDT-PERP 15m.

Discovered by Iter4 robustness sweep (commits f5222f3 / 263a9de).
Spec:
  - Bar:    15m
  - Entry:  OI(24h) pct_change < -2.0%  AND  close(24h) pct_change < -0.5%
  - Side:   LONG only
  - TP:     +2.0% (system-driven via take_profit_pct)
  - SL:     -1.2% (system-driven via stop_loss_pct)
  - Hold:   max 48 bars (12h) — time-based forced exit
  - Cooldown: no overlapping positions

Data dependency:
  - OI is provided by `indicators.oi_provider.get_oi_provider`.
    * Backtest: reads `data/perp_meta/BTCUSDT_oi_5m.parquet` from disk.
    * Live: reads from Redis (`oi:BTCUSDT:hist`) populated by `scripts/oi_ingestor.py`.

Live integration notes:
  - Run with `--candle-interval 15m`. The 24h pct change requires 96 bars of
    close history, so the strategy stays flat for the first ~24h after start.
  - Set `stop_loss_pct=0.012` and `take_profit_pct=0.02` (or rely on the
    runner's stop-loss configuration). When the runner only supports SL via
    `stop_loss_pct`, the strategy enforces TP and time-exit internally.

Backtest/Live performance (full sample 2020-09..2026-04):
  +214% total, PF 1.34, DD 22.4%, 75% positive quarters, 91% positive 6m windows.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

# Allow strategy file to import from project src/ when loaded by run_backtest /
# the runner; both already insert src/ into sys.path, but be defensive for tests.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from strategy.base import Strategy
from strategy.context import StrategyContext

try:
    from indicators.oi_provider import get_oi_provider
except Exception as _exc:  # noqa: BLE001
    get_oi_provider = None  # type: ignore[assignment]
    _OI_IMPORT_ERR = _exc
else:
    _OI_IMPORT_ERR = None


STRATEGY_PARAMS: dict[str, Any] = {
    # Signal thresholds
    "oi_lookback_bars": 96,        # 24h on 15m bars
    "oi_drop_threshold": -0.020,   # OI must drop >= 2.0% over lookback
    "price_drop_threshold": -0.005,# price must drop >= 0.5% over lookback
    # Exit
    "tp_pct": 0.020,               # +2.0% take-profit
    "sl_pct": 0.012,               # -1.2% stop-loss (also enforced by system)
    "max_hold_bars": 48,           # 12h on 15m bars
    # Sizing
    "entry_pct": None,             # None => use system default
}


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {"name": "oi_lookback_bars", "type": "int", "min": 16, "max": 384,
     "label": "OI lookback (bars)"},
    {"name": "oi_drop_threshold", "type": "float", "min": -0.10, "max": 0.0,
     "step": 0.001, "label": "OI drop threshold"},
    {"name": "price_drop_threshold", "type": "float", "min": -0.10, "max": 0.0,
     "step": 0.001, "label": "Price drop threshold"},
    {"name": "tp_pct", "type": "float", "min": 0.001, "max": 0.10, "step": 0.001,
     "label": "Take profit %"},
    {"name": "sl_pct", "type": "float", "min": 0.001, "max": 0.10, "step": 0.001,
     "label": "Stop loss %"},
    {"name": "max_hold_bars", "type": "int", "min": 1, "max": 1000,
     "label": "Max hold (bars)"},
]


class OiCapitulationBottomStrategy(Strategy):
    """OI capitulation-bottom mean-reversion LONG strategy."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        params = {**STRATEGY_PARAMS, **kwargs}
        self.oi_lookback_bars = int(params["oi_lookback_bars"])
        self.oi_drop_threshold = float(params["oi_drop_threshold"])
        self.price_drop_threshold = float(params["price_drop_threshold"])
        self.tp_pct = float(params["tp_pct"])
        self.sl_pct = float(params["sl_pct"])
        self.max_hold_bars = int(params["max_hold_bars"])
        self.entry_pct = params["entry_pct"]

        # State
        self._closes: list[float] = []
        self._entry_bar_index: int | None = None
        self._entry_price: float | None = None
        self._bar_index: int = 0
        self._is_closing: bool = False
        self._oi_provider: Any = None
        self._prev_signal: bool = False  # for edge-triggered entry

        # Strategy meta (used by web UI / logs)
        self.params = params
        self.indicator_config = {}

    def initialize(self, ctx: StrategyContext) -> None:
        if get_oi_provider is None:
            raise RuntimeError(
                f"OI provider not importable: {_OI_IMPORT_ERR}. "
                "Verify src/indicators/oi_provider.py is on PYTHONPATH."
            )
        symbol = getattr(ctx, "symbol", "BTCUSDT")
        self._oi_provider = get_oi_provider(symbol)
        self._closes = []
        self._entry_bar_index = None
        self._entry_price = None
        self._bar_index = 0
        self._is_closing = False
        self._prev_signal = False

        # Register a thin wrapper as a custom indicator for the dashboard.
        def _oi_pct_change_24h(_inner_ctx: Any) -> float:
            ts = _bar_timestamp_from_ctx(ctx)
            if ts <= 0:
                return float("nan")
            return float(self._oi_provider.pct_change(ts))

        try:
            ctx.register_indicator("oi_pct_change_24h", _oi_pct_change_24h)
        except Exception:  # noqa: BLE001
            # Some lightweight contexts may not support register_indicator;
            # the strategy still works because it queries the provider directly.
            pass

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # Reset closing flag once flat
        if ctx.position_size == 0:
            self._is_closing = False
            self._entry_bar_index = None
            self._entry_price = None

        close = float(bar.get("close", bar.get("price", 0.0)))
        if not math.isfinite(close) or close <= 0:
            return
        high = float(bar.get("high", close) or close)
        low = float(bar.get("low", close) or close)
        # `price` is the simulated tick price for the current sub-step in
        # backtest (open, then low for longs, then close). In live it equals
        # close. Use it as the intrabar fill reference for SL/TP.
        tick_price = float(bar.get("price", close) or close)

        is_new_bar = bool(bar.get("is_new_bar", True))

        # ---- Exit checks: run on EVERY sub-tick (open / low / close) so that
        # we honor intrabar TP and SL fills, matching the discovery sweep.
        if ctx.position_size > 0 and self._entry_price is not None and not self._is_closing:
            tp_level = self._entry_price * (1.0 + self.tp_pct)
            sl_level = self._entry_price * (1.0 - self.sl_pct)
            # Pessimistic ordering when both touch in the same bar:
            # assume SL hits first.
            if low <= sl_level or tick_price <= sl_level:
                self._is_closing = True
                ctx.close_position(reason=f"OI: SL -{self.sl_pct * 100:.1f}%")
                return
            if high >= tp_level or tick_price >= tp_level:
                self._is_closing = True
                ctx.close_position(reason=f"OI: TP +{self.tp_pct * 100:.1f}%")
                return
            if (is_new_bar and self._entry_bar_index is not None and
                    self._bar_index - self._entry_bar_index >= self.max_hold_bars):
                self._is_closing = True
                ctx.close_position(reason=f"OI: time exit ({self.max_hold_bars} bars)")
                return

        # ---- Entry: only on the close of a new 15m bar.
        if not is_new_bar:
            return

        # Open-orders guard (live)
        try:
            open_orders = ctx.get_open_orders() or []
        except Exception:  # noqa: BLE001
            open_orders = []
        if open_orders:
            return

        self._closes.append(close)
        if len(self._closes) > 4096:
            self._closes = self._closes[-4096:]
        self._bar_index += 1

        if ctx.position_size != 0:
            return
        if len(self._closes) <= self.oi_lookback_bars:
            return

        # Entry only when flat and warmup is satisfied
        if ctx.position_size != 0:
            return
        if len(self._closes) <= self.oi_lookback_bars:
            return

        # 24h price pct change
        ref_close = self._closes[-(self.oi_lookback_bars + 1)]
        if ref_close <= 0:
            return
        price_chg = close / ref_close - 1.0

        # 24h OI pct change (provider handles parquet vs Redis)
        ts = _bar_timestamp_from_bar(bar)
        if ts <= 0:
            return
        try:
            oi_chg = float(self._oi_provider.pct_change(ts))
        except Exception:  # noqa: BLE001
            return
        if not math.isfinite(oi_chg):
            return  # skip when OI history not yet available

        signal_now = (oi_chg <= self.oi_drop_threshold and
                      price_chg <= self.price_drop_threshold)
        # Edge-triggered: only enter on the rising edge of the signal cluster.
        # This matches the discovery sweep, which counts one trade per cluster.
        if signal_now and not self._prev_signal:
            reason = (f"OI capitulation: oi_24h={oi_chg * 100:.2f}% "
                      f"price_24h={price_chg * 100:.2f}%")
            entry_pct = self.entry_pct
            if entry_pct is None:
                ctx.enter_long(reason=reason)
            else:
                ctx.enter_long(reason=reason, entry_pct=float(entry_pct))
            self._entry_bar_index = self._bar_index
            self._entry_price = close
        self._prev_signal = signal_now


# ---- bar timestamp resolution helpers ---------------------------------------
def _bar_timestamp_from_bar(bar: dict[str, Any]) -> int:
    """Return bar-open timestamp in ms for the current 15m bar.

    - Backtest engine sets `bar_timestamp` (bar open) and `timestamp` (close).
    - Live engine sets `timestamp` (bar open).
    """
    for key in ("bar_timestamp", "timestamp"):
        try:
            v = int(bar.get(key, 0) or 0)
        except Exception:  # noqa: BLE001
            v = 0
        if v > 0:
            return v
    return 0


def _bar_timestamp_from_ctx(ctx: StrategyContext) -> int:
    """Best-effort bar-open timestamp from the context for the indicator wrapper."""
    for attr in ("_current_timestamp", "current_timestamp"):
        v = getattr(ctx, attr, None)
        if v:
            try:
                return int(v)
            except Exception:  # noqa: BLE001
                pass
    return 0
