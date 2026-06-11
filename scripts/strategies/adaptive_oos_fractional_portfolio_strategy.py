"""Strict-OOS adaptive fractional portfolio strategy for BTCUSDT-PERP.

This strategy is intentionally separate from ``multi_factor_portfolio_strategy``.
It uses the same causal MFP signal families as a candidate pool, but does not
freeze parameters selected on the full sample. For every calendar month it
selects candidate weights using only months that ended before that month, then
trades a small quantized fractional exposure during the month.

Runtime contract:
  - Base candle interval: 15m.
  - Uses the checked-in strict-OOS exposure cache by default so AlphaWeaver
    backtests do not rebuild the heavy research model on every run.
  - The first ``warmup_months`` are research warmup; trading starts afterward.
"""

# ruff: noqa: PLR0912, PLR0913
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from scipy.optimize import linprog as _linprog
except Exception:  # noqa: BLE001
    _linprog = None

_THIS_DIR = Path(__file__).resolve().parent
_SRC = Path(__file__).resolve().parents[2] / "src"
for _p in (str(_THIS_DIR), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import multi_factor_portfolio_strategy as mfp  # noqa: E402

from strategy.base import Strategy  # noqa: E402
from strategy.context import StrategyContext  # noqa: E402

STRATEGY_PARAMS: dict[str, Any] = {
    "use_precomputed_cache": True,
    "allow_slow_rebuild": False,
    "warmup_months": 12,
    "max_weight_per_leg": 0.08,
    "max_selected_legs": 120,
    "selector_min_pos_month_pct": 0.50,
    "base_scale": 1.0,
    "exposure_step": 0.10,
    "month_profit_lock_pct": 0.001,
    "month_loss_stop_pct": -0.02,
    "recovery_multiplier": 3.0,
    "max_recovery_exposure": 0.75,
    "probe_exposure": 0.005,
    "model_commission_rate": 0.0004,
}

_CACHE_PATH = _THIS_DIR / "_cache" / "adaptive_oos_fractional_model_v1.npz"


def _don(
    interval_min: int,
    dc_period: int,
    tp: float,
    sl: float,
    hold_h: int,
    side: str,
) -> dict[str, Any]:
    return {
        "family": "donchian_breakout",
        "config": {
            "interval_min": interval_min,
            "dc_period": dc_period,
            "atr_min_mult": 0.0,
            "use_oi": False,
            "oi_lb": 96,
            "oi_min_for_long": 0.0,
            "oi_max_for_short": -0.01,
            "tp_pct": tp,
            "sl_pct": sl,
            "max_hold_h": hold_h,
            "side": side,
        },
    }


def _hold_for(interval_min: int) -> int:
    return {30: 24, 60: 48, 120: 72, 240: 96}.get(interval_min, 48)


def _candidate_pool() -> list[dict[str, Any]]:
    """Diverse causal MFP-family pool used by the monthly selector."""
    base = mfp.ALL_LEGS
    pool: list[dict[str, Any]] = []
    for interval_min in (15, 30, 60, 120, 240):
        for dc_period in (10, 20, 48, 96, 192):
            for side in ("both", "long_only", "short_only"):
                for tp, sl in ((0.04, 0.015), (0.08, 0.025), (0.15, 0.04)):
                    for hold_mult in (0.5, 1.0):
                        hold = max(2, int(_hold_for(interval_min) * hold_mult))
                        pool.append(_don(interval_min, dc_period, tp, sl, hold, side))

    for i in (1, 2, 3, 4):
        for side in ("both", "long_only", "short_only"):
            for hold in (8, 24):
                cfg = {**base[i]["config"], "side": side, "max_hold_h": hold}
                pool.append({"family": base[i]["family"], "config": cfg})

    for side in ("both", "long_only", "short_only"):
        for hold in (8, 16):
            cfg = {**base[0]["config"], "side": side, "max_hold_h": hold}
            pool.append({"family": base[0]["family"], "config": cfg})

    for i in (10, 11, 12, 13, 14):
        for side in ("both", "long_only", "short_only"):
            for hold in (8, 16, 24):
                cfg = {**base[i]["config"], "side": side, "max_hold_h": hold}
                pool.append({"family": base[i]["family"], "config": cfg})

    for i in (5, 6, 7, 8, 9):
        for side in ("both", "long_only", "short_only"):
            for hold in (4, 8, 16):
                cfg = {**base[i]["config"], "side": side, "max_hold_h": hold}
                pool.append({"family": base[i]["family"], "config": cfg})
    return pool


def _hours_to_bars(hours: float, interval_min: int) -> int:
    return max(1, int(round(hours * 60 / interval_min)))


def _leg_side_series_tf(
    df_tf: pd.DataFrame,
    long_sig: np.ndarray,
    short_sig: np.ndarray,
    tp_pct: float,
    sl_pct: float,
    max_hold_bars: int,
) -> np.ndarray:
    open_ = df_tf["open"].to_numpy("f8")
    high = df_tf["high"].to_numpy("f8")
    low = df_tf["low"].to_numpy("f8")
    close = df_tf["close"].to_numpy("f8")
    out = np.zeros(len(close), dtype="i1")
    side = 0
    entry_price = 0.0
    entry_idx = -1
    for i in range(len(close)):
        exited = False
        if side != 0 and entry_idx >= 0:
            if side > 0:
                tp = entry_price * (1.0 + tp_pct)
                sl = entry_price * (1.0 - sl_pct)
                if open_[i] <= sl or low[i] <= sl or high[i] >= tp:
                    side = 0
                    entry_idx = -1
                    exited = True
            else:
                tp = entry_price * (1.0 - tp_pct)
                sl = entry_price * (1.0 + sl_pct)
                if open_[i] >= sl or high[i] >= sl or low[i] <= tp:
                    side = 0
                    entry_idx = -1
                    exited = True
            if not exited and side != 0 and (i - entry_idx) >= max_hold_bars:
                side = 0
                entry_idx = -1
                exited = True
        if not exited and side == 0:
            if i < len(long_sig) and bool(long_sig[i]):
                side = 1
                entry_price = float(close[i])
                entry_idx = i
            elif i < len(short_sig) and bool(short_sig[i]):
                side = -1
                entry_price = float(close[i])
                entry_idx = i
        out[i] = side
    return out


def _leg_sides_on_15m(
    leg: dict[str, Any],
    unified: pd.DataFrame,
    close_ms15: np.ndarray,
) -> np.ndarray:
    cfg = leg["config"]
    interval_min = int(cfg["interval_min"])
    df_tf = mfp._resample_to(unified, interval_min)
    long_sig, short_sig = mfp._SIG_FUNCS[leg["family"]](df_tf, cfg)
    side_tf = _leg_side_series_tf(
        df_tf,
        long_sig,
        short_sig,
        float(cfg["tp_pct"]),
        float(cfg["sl_pct"]),
        _hours_to_bars(float(cfg["max_hold_h"]), interval_min),
    )
    tf_close_ms = df_tf["ts"].to_numpy("int64") + interval_min * 60_000
    pos = np.searchsorted(tf_close_ms, close_ms15, side="right") - 1
    out = np.zeros(len(close_ms15), dtype="i1")
    valid = pos >= 0
    out[valid] = side_tf[pos[valid]]
    return out


def _solve_maximin_weights(monthly: np.ndarray, cols: np.ndarray, wmax: float) -> np.ndarray:
    """Maximize the worst prior monthly return with non-negative capped weights."""
    if _linprog is None:
        return np.full(len(cols), 1.0 / max(len(cols), 1), dtype="f8")

    x = monthly[:, cols]
    n_months, n_legs = x.shape
    c = np.zeros(n_legs + 1, dtype="f8")
    c[-1] = -1.0
    a_ub = np.hstack([-x, np.ones((n_months, 1), dtype="f8")])
    a_eq = np.zeros((1, n_legs + 1), dtype="f8")
    a_eq[0, :n_legs] = 1.0
    result = _linprog(
        c,
        A_ub=a_ub,
        b_ub=np.zeros(n_months, dtype="f8"),
        A_eq=a_eq,
        b_eq=np.array([1.0], dtype="f8"),
        bounds=[(0.0, float(wmax))] * n_legs + [(None, None)],
        method="highs",
    )
    if not result.success:
        return np.full(n_legs, 1.0 / max(n_legs, 1), dtype="f8")
    return np.asarray(result.x[:n_legs], dtype="f8")


def _build_monthly_model(
    unified: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    ts = unified["ts"].to_numpy("int64")
    close = unified["close"].to_numpy("f8")
    close_ms15 = ts + 15 * 60_000
    pool = _candidate_pool()
    sides = np.zeros((len(pool), len(ts)), dtype="i1")
    for i, leg in enumerate(pool):
        sides[i] = _leg_sides_on_15m(leg, unified, close_ms15)

    bar_ret = np.zeros(len(ts), dtype="f8")
    bar_ret[1:] = close[1:] / close[:-1] - 1.0
    held = np.zeros_like(sides, dtype="f8")
    held[:, 1:] = sides[:, :-1]
    turn = np.zeros_like(sides, dtype="f8")
    turn[:, 0] = np.abs(sides[:, 0])
    turn[:, 1:] = np.abs(np.diff(sides.astype("i2"), axis=1))
    leg_ret = held * bar_ret - turn * float(params["model_commission_rate"])

    dt = pd.to_datetime(ts, unit="ms", utc=True)
    month_starts = pd.Series(1, index=dt).resample("MS").count().index
    masks: list[np.ndarray] = []
    monthly_rows: list[np.ndarray] = []
    monthly_trades: list[np.ndarray] = []
    for month_start in month_starts:
        month_end = month_start + pd.offsets.MonthBegin(1)
        mask = np.asarray((dt >= month_start) & (dt < month_end))
        if not mask.any():
            continue
        masks.append(mask)
        monthly_rows.append(leg_ret[:, mask].sum(axis=1))
        monthly_trades.append((turn[:, mask] > 0).sum(axis=1))

    monthly = np.vstack(monthly_rows)
    monthly_trade_counts = np.vstack(monthly_trades)
    base = np.zeros(len(ts), dtype="f8")
    warmup = int(params["warmup_months"])
    max_selected = int(params["max_selected_legs"])
    pos_floor = float(params["selector_min_pos_month_pct"])
    for month_idx in range(warmup, len(masks)):
        train = monthly[:month_idx]
        mean = train.mean(axis=0)
        pos_pct = (train > 0).mean(axis=0)
        worst = train.min(axis=0)
        trade_count = monthly_trade_counts[:month_idx].sum(axis=0)
        min_trades = max(10, month_idx * 2)
        eligible = (mean > 0.0) & (pos_pct >= pos_floor) & (trade_count >= min_trades)
        cols = np.where(eligible)[0]
        if len(cols) > max_selected:
            score = mean * 2.0 + pos_pct * 0.01 + np.maximum(worst, -0.1)
            cols = cols[np.argsort(score[cols])[-max_selected:]]
        if len(cols) < 5:
            cols = np.argsort(mean)[-min(50, len(mean)):]
        weights = _solve_maximin_weights(
            monthly[:month_idx],
            cols,
            float(params["max_weight_per_leg"]),
        )
        raw = weights @ sides[cols][:, masks[month_idx]]
        scaled = np.clip(raw * float(params["base_scale"]), -1.0, 1.0)
        step = float(params["exposure_step"])
        base[masks[month_idx]] = np.round(scaled / step) * step if step > 0 else scaled
    return ts, base


def _load_precomputed_model() -> tuple[np.ndarray, np.ndarray] | None:
    if not _CACHE_PATH.exists():
        return None
    try:
        with np.load(_CACHE_PATH) as data:
            ts = np.asarray(data["ts"], dtype="int64")
            base = np.asarray(data["base"], dtype="f8")
    except (OSError, KeyError, ValueError) as exc:
        raise RuntimeError(f"failed to load AOF model cache: {_CACHE_PATH}") from exc
    if ts.ndim != 1 or base.ndim != 1 or ts.size != base.size:
        raise RuntimeError(
            f"invalid AOF model cache shape: ts={ts.shape}, base={base.shape}"
        )
    return ts, base


class AdaptiveOosFractionalPortfolioStrategy(Strategy):
    """Monthly train-only selector + fractional exposure guard."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self.params = {**STRATEGY_PARAMS, **kwargs}
        self.symbol = "BTCUSDT"
        self._model_ts = np.empty(0, dtype="int64")
        self._base_exposure = np.empty(0, dtype="f8")
        self._last_bar_ts = 0
        self._month_key: str | None = None
        self._month_start_equity = 0.0
        self._month_locked = False
        self._current_target = 0.0

    def initialize(self, ctx: StrategyContext) -> None:
        symbol = str(getattr(ctx, "symbol", "BTCUSDT")).upper()
        if symbol != "BTCUSDT":
            raise ValueError(
                "AdaptiveOosFractionalPortfolioStrategy currently supports BTCUSDT only"
            )
        self.symbol = symbol
        cached = (
            _load_precomputed_model()
            if bool(self.params["use_precomputed_cache"])
            else None
        )
        if cached is not None:
            self._model_ts, self._base_exposure = cached
        else:
            if not bool(self.params["allow_slow_rebuild"]):
                raise RuntimeError(
                    "AOF model cache is missing. Restore "
                    f"{_CACHE_PATH} or set allow_slow_rebuild=True for a slow "
                    "research-model rebuild."
                )
            unified = mfp._load_unified_dataset(symbol)
            self._model_ts, self._base_exposure = _build_monthly_model(
                unified,
                self.params,
            )
        self._last_bar_ts = 0
        self._month_key = None
        self._month_start_equity = 0.0
        self._month_locked = False
        self._current_target = 0.0

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if not bool(bar.get("is_new_bar", True)):
            return
        ts = int(bar.get("bar_timestamp", bar.get("timestamp", 0)) or 0)
        if ts <= 0 or ts == self._last_bar_ts:
            return
        self._last_bar_ts = ts

        price = float(bar.get("close", bar.get("price", 0.0)) or 0.0)
        if price <= 0.0:
            return

        month_key = pd.Timestamp(ts, unit="ms", tz="UTC").strftime("%Y-%m")
        equity = float(getattr(ctx, "total_equity", ctx.balance) or 0.0)
        if month_key != self._month_key:
            self._month_key = month_key
            self._month_start_equity = equity if equity > 0.0 else float(ctx.balance)
            self._month_locked = False

        base = self._base_for_ts(ts)
        month_return = (
            equity / self._month_start_equity - 1.0
            if self._month_start_equity > 0.0 else 0.0
        )

        if self._month_locked:
            target = float(self.params["probe_exposure"]) * (
                1.0 if base > 0 else (-1.0 if base < 0 else 0.0)
            )
        else:
            multiplier = float(self.params["recovery_multiplier"]) if month_return < 0.0 else 1.0
            target = float(np.clip(
                base * multiplier,
                -float(self.params["max_recovery_exposure"]),
                float(self.params["max_recovery_exposure"]),
            ))

        self._rebalance(ctx, target, price)
        self._current_target = target

        if not self._month_locked:
            updated_equity = float(getattr(ctx, "total_equity", ctx.balance) or equity)
            updated_month_return = (
                updated_equity / self._month_start_equity - 1.0
                if self._month_start_equity > 0.0 else month_return
            )
            if (
                updated_month_return >= float(self.params["month_profit_lock_pct"])
                or updated_month_return <= float(self.params["month_loss_stop_pct"])
            ):
                self._month_locked = True

    def _base_for_ts(self, ts: int) -> float:
        if self._model_ts.size == 0:
            return 0.0
        idx = int(np.searchsorted(self._model_ts, int(ts), side="left"))
        if idx >= self._model_ts.size or int(self._model_ts[idx]) != int(ts):
            return 0.0
        return float(self._base_exposure[idx])

    def _rebalance(self, ctx: StrategyContext, target_exposure: float, price: float) -> None:
        equity = float(getattr(ctx, "total_equity", ctx.balance) or 0.0)
        leverage = float(getattr(ctx, "leverage", 1.0) or 1.0)
        if equity <= 0.0 or leverage <= 0.0:
            return
        current_qty = float(ctx.position_size)
        target_notional = equity * leverage * float(target_exposure)
        target_qty = target_notional / price
        delta_qty = target_qty - current_qty
        min_qty = max(abs(target_qty), abs(current_qty), 1.0) * 1e-4
        if abs(delta_qty) <= min_qty:
            return
        reason = f"AOF: target_exposure={target_exposure:+.3f}"
        if delta_qty > 0:
            ctx.buy(abs(delta_qty), reason=reason)
        else:
            ctx.sell(abs(delta_qty), reason=reason)
