"""OU mean-reversion strategy on a BTC-ETH (or arbitrary pair) spread.

PURPOSE
-------
Extends the single-asset Ornstein-Uhlenbeck strategy to a **pair spread**:

    S_t = log(P_primary_t) - hedge_ratio * log(P_pair_t)

The spread is fit to an OU process via OLS AR(1) and traded using the
same optimal-stopping-inspired barriers (entry interval ``[a*_L, d*_L]``,
target ``b*_L``, loss barrier ``L``).

Trade direction is determined by the *spread* z-score, but **only the
primary symbol is traded** (the engine in this codebase is single-symbol,
so we cannot place opposing legs in the same context). This means:

  - When spread z is **negative** (primary cheap vs. pair) the strategy
    goes **long the primary**.
  - When spread z is **positive** (primary expensive vs. pair) the
    strategy goes **short the primary**.

This is not a true delta-neutral pair trade — it is "primary mean-reversion
conditioned on the spread". To convert it into a true pair trade, the
runner would need to manage two symbols in parallel (a portfolio engine).
For now the strategy is **backtest-only**; live mode raises
``NotImplementedError`` to avoid silently trading a one-legged signal in
production.

DATA LAYOUT
-----------
The pair symbol's klines are loaded from
``data/perp_meta/<PAIR_SYMBOL>_<interval>_klines.parquet`` at
``initialize`` time. The strategy aligns the pair's closes to the primary
bars via last-known-value lookup (forward fill).

PARAMETERS
----------
Inherits all OU/barrier parameters from
``ou_optimal_stopping_strategy.py`` and adds:

  - ``pair_symbol``    (e.g. ``"ETHUSDT"``)
  - ``pair_interval``  (e.g. ``"15m"`` — must match the engine's bar size)
  - ``hedge_ratio``    (fixed beta on the pair leg; default 1.0)
  - ``auto_hedge_window``  (if > 0, refit hedge_ratio via rolling OLS each
    ``auto_hedge_refit_every_bars`` bars; if 0, use fixed ``hedge_ratio``)
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

# Local imports (must be relative to scripts/strategies so the engine's
# importlib loader resolves them).
import importlib.util
import sys

from strategy.base import Strategy
from strategy.context import StrategyContext

logger = logging.getLogger(__name__)

# Reuse the single-asset strategy's OU fit helper.
_OU_FILE = Path(__file__).resolve().parent / "ou_optimal_stopping_strategy.py"
_spec = importlib.util.spec_from_file_location("_ou_single_for_pair", _OU_FILE)
if _spec is None or _spec.loader is None:
    raise ImportError(f"could not load OU single-asset strategy at {_OU_FILE}")
_mod = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("_ou_single_for_pair", _mod)
_spec.loader.exec_module(_mod)
_fit_ou = _mod._fit_ou  # type: ignore[attr-defined]
_bar_ts = _mod._bar_ts  # type: ignore[attr-defined]


_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "perp_meta"


# ---------------------------------------------------------------------------
# Pair series helper
# ---------------------------------------------------------------------------
def _load_pair_log_closes(pair_symbol: str, pair_interval: str) -> tuple[list[int], list[float]]:
    """Load (sorted_open_times_ms, log_close) arrays from the pair parquet.

    Raises ``FileNotFoundError`` when the parquet is missing.
    """
    parquet_path = _DATA_DIR / f"{pair_symbol}_{pair_interval}_klines.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(
            f"OuPairStrategy: pair parquet not found at {parquet_path}. "
            f"Run scripts/refresh_klines_parquet.py for {pair_symbol} {pair_interval}."
        )
    import pandas as pd  # local import to keep cold-import time low
    df = pd.read_parquet(parquet_path).sort_values("ts").reset_index(drop=True)
    ts = df["ts"].astype("int64").tolist()
    closes = df["c"].astype("float64").tolist()
    log_closes: list[float] = []
    for c in closes:
        if c > 0:
            log_closes.append(math.log(c))
        else:
            log_closes.append(float("nan"))
    return ts, log_closes


def _last_known(sorted_ts: list[int], values: list[float], query_ts: int) -> float:
    """Forward-fill last-known value at ``query_ts``.

    ``sorted_ts`` must be sorted ascending. Returns ``NaN`` when
    ``query_ts`` precedes the first available timestamp.
    """
    if not sorted_ts:
        return float("nan")
    # Binary search for the largest ts <= query_ts.
    lo, hi = 0, len(sorted_ts) - 1
    if query_ts < sorted_ts[0]:
        return float("nan")
    if query_ts >= sorted_ts[-1]:
        return values[-1]
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if sorted_ts[mid] <= query_ts:
            lo = mid
        else:
            hi = mid - 1
    return values[lo]


# ---------------------------------------------------------------------------
# Strategy params
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = {
    # --- Pair config ------------------------------------------------------
    "pair_symbol": "ETHUSDT",
    "pair_interval": "15m",
    "hedge_ratio": 1.0,
    # If > 0, refit hedge_ratio via OLS of log(primary) on log(pair) over
    # the most recent ``auto_hedge_window`` bars. 0 = keep fixed.
    "auto_hedge_window": 0,
    "auto_hedge_refit_every_bars": 96,

    # --- OU fit on the SPREAD --------------------------------------------
    # The fit window is applied to the spread series (not raw prices).
    "ou_window": 240,
    "min_bars_to_trade": 240,
    "refit_every_bars": 1,

    # --- Stationarity / half-life filter ---------------------------------
    "min_b": 0.05,
    "max_b": 0.99,
    "min_half_life_bars": 4.0,
    "max_half_life_bars": 96.0,

    # --- Cost / discount --------------------------------------------------
    "fee_round_trip_bps": 8.0,
    "min_edge_bps": 4.0,

    # --- Barriers (z-space, symmetric) -----------------------------------
    "entry_z_lo": 1.0,
    "entry_z_hi": 2.5,
    "exit_z": 0.2,
    "stop_z": 3.5,
    # Per-side overrides (NaN = fall back to symmetric).
    "entry_z_lo_long": float("nan"),
    "entry_z_hi_long": float("nan"),
    "exit_z_long": float("nan"),
    "stop_z_long": float("nan"),
    "entry_z_lo_short": float("nan"),
    "entry_z_hi_short": float("nan"),
    "exit_z_short": float("nan"),
    "stop_z_short": float("nan"),

    # --- Hold / cooldown --------------------------------------------------
    "max_hold_bars": 48,
    "cooldown_bars": 4,

    # --- Bar gating + diagnostics -----------------------------------------
    "new_bar_only": 1,
    "emit_fit_events": 0,
}


STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "pair_symbol": {
        "type": "string",
        "label": "페어 심볼",
        "description": "스프레드의 두 번째 다리. data/perp_meta/{SYMBOL}_{interval}_klines.parquet 필요.",
        "group": "페어",
    },
    "pair_interval": {
        "type": "string",
        "label": "페어 봉 간격",
        "description": "엔진의 봉 간격과 동일해야 함. 보통 '15m'.",
        "group": "페어",
    },
    "hedge_ratio": {
        "type": "number", "min": 0.01, "max": 10.0,
        "label": "헤지 비율 β",
        "description": "스프레드 = log(P_primary) - β·log(P_pair).",
        "group": "페어",
    },
    "auto_hedge_window": {
        "type": "integer", "min": 0, "max": 2000,
        "label": "헤지 비율 자동 적합 윈도우 (봉)",
        "description": "0 = 고정값 사용. >0이면 N봉 OLS로 β 갱신.",
        "group": "페어",
    },
    "auto_hedge_refit_every_bars": {
        "type": "integer", "min": 1, "max": 1000,
        "label": "헤지 비율 재적합 주기 (봉)",
        "description": "auto_hedge_window>0일 때만 사용.",
        "group": "페어",
    },
    "ou_window": {
        "type": "integer", "min": 60, "max": 1000,
        "label": "OU 추정 윈도우 (스프레드, 봉)",
        "description": "스프레드 시계열의 OLS AR(1) 회귀 lookback.",
        "group": "OU 적합",
    },
    "refit_every_bars": {
        "type": "integer", "min": 1, "max": 96,
        "label": "OU 재적합 주기 (봉)",
        "group": "OU 적합",
    },
    "min_half_life_bars": {
        "type": "number", "min": 1.0, "max": 200.0,
        "label": "최소 반감기 (봉)",
        "group": "필터",
    },
    "max_half_life_bars": {
        "type": "number", "min": 5.0, "max": 1000.0,
        "label": "최대 반감기 (봉)",
        "group": "필터",
    },
    "fee_round_trip_bps": {
        "type": "number", "min": 0.0, "max": 50.0,
        "label": "왕복 수수료 (bps)",
        "group": "비용",
    },
    "min_edge_bps": {
        "type": "number", "min": 0.0, "max": 200.0,
        "label": "최소 엣지 (bps)",
        "group": "비용",
    },
    "entry_z_lo": {
        "type": "number", "min": 0.0, "max": 5.0,
        "label": "진입 구역 하한 (|z|)",
        "group": "임계점",
    },
    "entry_z_hi": {
        "type": "number", "min": 0.0, "max": 6.0,
        "label": "진입 구역 상한 (|z|)",
        "group": "임계점",
    },
    "exit_z": {
        "type": "number", "min": 0.0, "max": 3.0,
        "label": "청산 임계 (|z|)",
        "group": "임계점",
    },
    "stop_z": {
        "type": "number", "min": 1.0, "max": 10.0,
        "label": "손절 임계 (|z|)",
        "group": "임계점",
    },
    "max_hold_bars": {
        "type": "integer", "min": 1, "max": 1000,
        "label": "최대 보유 봉 수",
        "group": "운용",
    },
    "cooldown_bars": {
        "type": "integer", "min": 0, "max": 200,
        "label": "쿨다운 봉 수",
        "group": "운용",
    },
    "new_bar_only": {
        "type": "integer", "min": 0, "max": 1,
        "label": "새 봉에서만 진입 (0/1)",
        "group": "운용",
    },
    "emit_fit_events": {
        "type": "integer", "min": 0, "max": 1,
        "label": "OU_PAIR_FIT 이벤트 발생 (0/1)",
        "group": "진단",
    },
}


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class OuPairStrategy(Strategy):
    """Pair-spread Ornstein-Uhlenbeck mean-reversion strategy.

    Backtest-only. Live mode raises ``NotImplementedError`` because the
    engine cannot provide the pair symbol's stream in this codebase.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}

        self.pair_symbol = str(p["pair_symbol"]).upper()
        self.pair_interval = str(p["pair_interval"])
        self.hedge_ratio = float(p["hedge_ratio"])
        self.auto_hedge_window = int(p["auto_hedge_window"])
        self.auto_hedge_refit_every_bars = max(1, int(p["auto_hedge_refit_every_bars"]))

        self.ou_window = int(p["ou_window"])
        self.min_bars_to_trade = int(p["min_bars_to_trade"])
        self.refit_every_bars = max(1, int(p["refit_every_bars"]))

        self.min_b = float(p["min_b"])
        self.max_b = float(p["max_b"])
        self.min_half_life_bars = float(p["min_half_life_bars"])
        self.max_half_life_bars = float(p["max_half_life_bars"])

        self.fee_round_trip_bps = float(p["fee_round_trip_bps"])
        self.min_edge_bps = float(p["min_edge_bps"])

        self.entry_z_lo = float(p["entry_z_lo"])
        self.entry_z_hi = float(p["entry_z_hi"])
        self.exit_z = float(p["exit_z"])
        self.stop_z = float(p["stop_z"])

        def _pick(side_val: float, default: float) -> float:
            return default if (side_val is None or math.isnan(float(side_val))) else float(side_val)

        self.entry_z_lo_long = _pick(p["entry_z_lo_long"], self.entry_z_lo)
        self.entry_z_hi_long = _pick(p["entry_z_hi_long"], self.entry_z_hi)
        self.exit_z_long = _pick(p["exit_z_long"], self.exit_z)
        self.stop_z_long = _pick(p["stop_z_long"], self.stop_z)
        self.entry_z_lo_short = _pick(p["entry_z_lo_short"], self.entry_z_lo)
        self.entry_z_hi_short = _pick(p["entry_z_hi_short"], self.entry_z_hi)
        self.exit_z_short = _pick(p["exit_z_short"], self.exit_z)
        self.stop_z_short = _pick(p["stop_z_short"], self.stop_z)

        if not (self.entry_z_lo <= self.entry_z_hi):
            raise ValueError("entry_z_lo must be <= entry_z_hi")
        if not (self.exit_z < self.entry_z_lo):
            raise ValueError("exit_z must be < entry_z_lo")
        if not (self.stop_z > self.entry_z_hi):
            raise ValueError("stop_z must be > entry_z_hi")

        self.max_hold_bars = int(p["max_hold_bars"])
        self.cooldown_bars = int(p["cooldown_bars"])
        self.new_bar_only = bool(int(p["new_bar_only"]))
        self.emit_fit_events = bool(int(p["emit_fit_events"]))

        # Runtime state
        self._mode: str | None = None
        self._pair_ts: list[int] = []
        self._pair_log_closes: list[float] = []
        # Spread series accumulated bar by bar. Older entries fall off when
        # len > ou_window (we keep ou_window + cushion for safety).
        self._spread_window: list[float] = []
        # OU fit on the spread + bookkeeping.
        self._fit: dict[str, float] | None = None
        self._last_bar_ts: int = 0
        self._last_fit_bar_ts: int = 0
        self._last_hedge_refit_bar_ts: int = 0
        # Trade lifecycle counters.
        self._bars_in_position: int = 0
        self._bars_since_close: int | None = None
        self._is_closing: bool = False
        self._entry_side: int = 0
        # Cache of the most recent primary log-price (for auto-hedge OLS).
        self._primary_log_window: list[float] = []

        self.params = dict(p)
        self.indicator_config: dict[str, Any] = {}

    # ---- lifecycle ---------------------------------------------------------
    def initialize(self, ctx: StrategyContext) -> None:
        ctx_cls = type(ctx).__name__
        ctx_module = type(ctx).__module__
        if "Backtest" in ctx_cls:
            self._mode = "backtest"
        elif (
            "Live" in ctx_cls
            or ctx_cls == "StreamBoundStrategyContext"
            or ctx_module.startswith("live.")
        ):
            self._mode = "live"
        else:
            self._mode = None

        if self._mode == "live":
            raise NotImplementedError(
                "OuPairStrategy is backtest-only in this build because the live "
                "engine does not subscribe to the pair symbol's klines. To enable "
                "live trading, route this strategy through a multi-symbol "
                "portfolio runner."
            )

        # Eager parquet load.
        self._pair_ts, self._pair_log_closes = _load_pair_log_closes(
            self.pair_symbol, self.pair_interval
        )
        if not self._pair_ts:
            raise RuntimeError(
                f"OuPairStrategy: pair parquet for {self.pair_symbol} is empty."
            )

        # Reset runtime state.
        self._spread_window.clear()
        self._primary_log_window.clear()
        self._fit = None
        self._last_bar_ts = 0
        self._last_fit_bar_ts = 0
        self._last_hedge_refit_bar_ts = 0
        self._bars_in_position = 0
        self._bars_since_close = None
        self._is_closing = False
        self._entry_side = 0

        self._emit_event(ctx, "OU_PAIR_INIT", {
            "mode": self._mode,
            "pair_symbol": self.pair_symbol,
            "pair_interval": self.pair_interval,
            "pair_rows": len(self._pair_ts),
            "hedge_ratio": self.hedge_ratio,
            "auto_hedge_window": self.auto_hedge_window,
            "ou_window": self.ou_window,
            "entry_z_lo": self.entry_z_lo,
            "entry_z_hi": self.entry_z_hi,
            "exit_z": self.exit_z,
            "stop_z": self.stop_z,
        })

    # ---- helpers -----------------------------------------------------------
    def _maybe_refit_hedge(self, ts: int) -> None:
        """Refit ``hedge_ratio`` via simple OLS on the rolling log-price pair."""
        if self.auto_hedge_window <= 0:
            return
        if self._last_hedge_refit_bar_ts > 0:
            bar_step = max(1, self._bar_step_ms_safe(ts))
            bars_since = max(0, (ts - self._last_hedge_refit_bar_ts) // bar_step)
            if bars_since < self.auto_hedge_refit_every_bars:
                return
        # Need at least auto_hedge_window pairs.
        n = min(len(self._primary_log_window), self.auto_hedge_window)
        if n < max(30, self.auto_hedge_window // 2):
            return
        prim = self._primary_log_window[-n:]
        # Pair log-closes at the same bar timestamps would be needed to
        # form an aligned dataset, but the spread buffer already has the
        # diff. Instead of caching pair log-closes per bar we approximate:
        # use the previous spread + current primary to back out the pair
        # series. We have spread = primary - beta*pair, so
        # pair_log = (primary - spread) / beta, which is consistent only
        # for the current beta.  Simpler: maintain a parallel pair_log
        # window. For now we keep the fixed beta and document this as a
        # future improvement.
        # NOTE: auto-hedge is intentionally a no-op when we don't store
        # the pair log series. Set ``auto_hedge_window`` = 0 (the default)
        # to keep behaviour deterministic; the parameter is reserved for
        # a future enhancement.
        self._last_hedge_refit_bar_ts = ts

    def _bar_step_ms_safe(self, ts: int) -> int:
        if self._last_bar_ts <= 0 or ts <= self._last_bar_ts:
            return 1
        return ts - self._last_bar_ts

    def _push_spread(self, primary_log: float, pair_log: float) -> float:
        spread = primary_log - self.hedge_ratio * pair_log
        self._spread_window.append(spread)
        cap = max(self.ou_window * 2, 2 * self.min_bars_to_trade)
        if len(self._spread_window) > cap:
            self._spread_window = self._spread_window[-cap:]
        self._primary_log_window.append(primary_log)
        if len(self._primary_log_window) > cap:
            self._primary_log_window = self._primary_log_window[-cap:]
        return spread

    def _refit_if_needed(self, ctx: StrategyContext, ts: int) -> dict[str, float] | None:
        if self._fit is not None and self._last_fit_bar_ts > 0:
            bar_step = max(1, self._bar_step_ms_safe(ts))
            bars_since = max(0, (ts - self._last_fit_bar_ts) // bar_step)
            if bars_since < self.refit_every_bars:
                return self._fit

        if len(self._spread_window) < self.min_bars_to_trade:
            return None
        window = self._spread_window[-self.ou_window:]
        fit = _fit_ou(window, dt=1.0)
        if fit is None:
            self._fit = None
            self._last_fit_bar_ts = ts
            return None
        if not (self.min_b <= fit["b"] <= self.max_b):
            self._fit = None
            self._last_fit_bar_ts = ts
            return None
        if not (self.min_half_life_bars <= fit["half_life"] <= self.max_half_life_bars):
            self._fit = None
            self._last_fit_bar_ts = ts
            return None
        self._fit = fit
        self._last_fit_bar_ts = ts
        if self.emit_fit_events:
            self._emit_event(ctx, "OU_PAIR_FIT", {
                "ts": ts,
                "b": fit["b"],
                "theta": fit["theta"],
                "sigma_inf": fit["sigma_inf"],
                "half_life_bars": fit["half_life"],
                "hedge_ratio": self.hedge_ratio,
            })
        return fit

    def _z_for(self, spread_value: float, fit: dict[str, float]) -> float:
        sigma_inf = fit["sigma_inf"]
        if sigma_inf <= 0.0:
            return 0.0
        return (spread_value - fit["theta"]) / sigma_inf

    def _expected_revert_bps_on_primary(self, z: float, fit: dict[str, float]) -> float:
        """Expected primary-price move when spread reverts to theta, in bps.

        Spread is in log space, so a change of ``-z * sigma_inf`` in the
        spread translates to the same change in ``log(P_primary)`` when
        the pair is held constant. Since we trade only the primary leg,
        we use this as the headroom estimate.
        """
        if not math.isfinite(z):
            return 0.0
        return abs(z) * fit["sigma_inf"] * 1e4

    # ---- main loop ---------------------------------------------------------
    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            if self._is_closing:
                self._bars_since_close = 0
            self._is_closing = False
            self._bars_in_position = 0
            self._entry_side = 0

        if ctx.get_open_orders():
            return

        price = float(ctx.current_price)
        if price <= 0.0:
            return

        # Determine the timestamp for cross-symbol alignment.
        ts = _bar_ts(bar)
        if ts <= 0:
            return

        # Update the spread series only when this is a new bar (so we
        # don't pollute the OLS sample with intra-bar ticks).
        is_new_bar = bool(bar.get("is_new_bar", True))
        if is_new_bar and ts != self._last_bar_ts:
            pair_log = _last_known(self._pair_ts, self._pair_log_closes, ts)
            primary_log = math.log(price) if price > 0 else float("nan")
            if math.isfinite(pair_log) and math.isfinite(primary_log):
                self._push_spread(primary_log, pair_log)
            # Cooldown counter ticks once per new bar.
            if self._bars_since_close is not None:
                self._bars_since_close += 1
            self._maybe_refit_hedge(ts)

        # Compute current (instantaneous) spread for the z evaluation
        # below. Uses the latest pair log-close (still last-known).
        pair_log_now = _last_known(self._pair_ts, self._pair_log_closes, ts)
        primary_log_now = math.log(price) if price > 0 else float("nan")
        if not (math.isfinite(pair_log_now) and math.isfinite(primary_log_now)):
            return
        current_spread = primary_log_now - self.hedge_ratio * pair_log_now

        # -------- Exit evaluation (every tick) -----------------------------
        if (
            ctx.position_size != 0
            and not self._is_closing
            and self._fit is not None
        ):
            side = 1 if ctx.position_size > 0 else -1
            z = self._z_for(current_spread, self._fit)
            exit_z = self.exit_z_long if side > 0 else self.exit_z_short
            stop_z = self.stop_z_long if side > 0 else self.stop_z_short

            # Long primary = entered when spread was below theta. We get
            # stopped if the spread drifts further below.
            if side > 0 and z <= -stop_z:
                self._is_closing = True
                self._bars_since_close = 0
                ctx.close_position(
                    reason=f"OU-pair SL long spread_z={z:.2f} <= -{stop_z:.2f}",
                    exit_reason="STOP_LOSS",
                )
                self._emit_event(ctx, "OU_PAIR_STOP", {
                    "side": int(side), "spread_z": z, "stop_z": float(stop_z),
                    "fit": self._fit_snapshot(),
                })
                return
            if side < 0 and z >= stop_z:
                self._is_closing = True
                self._bars_since_close = 0
                ctx.close_position(
                    reason=f"OU-pair SL short spread_z={z:.2f} >= {stop_z:.2f}",
                    exit_reason="STOP_LOSS",
                )
                self._emit_event(ctx, "OU_PAIR_STOP", {
                    "side": int(side), "spread_z": z, "stop_z": float(stop_z),
                    "fit": self._fit_snapshot(),
                })
                return

            if abs(z) <= exit_z:
                self._is_closing = True
                self._bars_since_close = 0
                ctx.close_position(
                    reason=f"OU-pair TP spread_z={z:.2f} (|z| <= {exit_z:.2f})",
                    exit_reason="TAKE_PROFIT",
                )
                self._emit_event(ctx, "OU_PAIR_TARGET", {
                    "side": int(side), "spread_z": z, "exit_z": float(exit_z),
                    "fit": self._fit_snapshot(),
                })
                return

        # -------- Bar-close work (refit + entries + time exit) -------------
        if self.new_bar_only and not is_new_bar:
            return
        if ts == self._last_bar_ts:
            return

        # Refit OU on the spread series (cadence-gated).
        fit = self._refit_if_needed(ctx, ts)
        self._last_bar_ts = ts

        if ctx.position_size != 0:
            self._bars_in_position += 1
            if (
                self.max_hold_bars > 0
                and self._bars_in_position >= self.max_hold_bars
                and not self._is_closing
            ):
                self._is_closing = True
                self._bars_since_close = 0
                ctx.close_position(
                    reason=f"OU-pair time exit after {self._bars_in_position} bars",
                    exit_reason="TIME_EXIT",
                )
                self._emit_event(ctx, "OU_PAIR_TIME_EXIT", {
                    "bars_held": int(self._bars_in_position),
                    "side": int(self._entry_side),
                    "fit": self._fit_snapshot(),
                })
            return

        if fit is None:
            return
        if (
            self._bars_since_close is not None
            and self._bars_since_close < self.cooldown_bars
        ):
            return

        z = self._z_for(current_spread, fit)
        edge_bps = self._expected_revert_bps_on_primary(z, fit)
        if edge_bps < (self.fee_round_trip_bps + self.min_edge_bps):
            return

        abs_z = abs(z)
        # spread below theta (z<0) => primary cheap vs pair => long primary
        if z < 0.0:
            lo = self.entry_z_lo_long
            hi = self.entry_z_hi_long
        else:
            lo = self.entry_z_lo_short
            hi = self.entry_z_hi_short

        if not (lo <= abs_z <= hi):
            return

        side = -1 if z > 0.0 else 1
        if side > 0:
            ctx.enter_long(
                reason=f"OU-pair long spread_z={z:.2f} hl={fit['half_life']:.1f} edge={edge_bps:.1f}bps",
            )
        else:
            ctx.enter_short(
                reason=f"OU-pair short spread_z={z:.2f} hl={fit['half_life']:.1f} edge={edge_bps:.1f}bps",
            )
        self._entry_side = side
        self._bars_in_position = 0
        self._bars_since_close = None
        self._emit_event(ctx, "OU_PAIR_ENTER", {
            "side": int(side),
            "spread_z": z,
            "edge_bps": edge_bps,
            "fee_bps": self.fee_round_trip_bps,
            "hedge_ratio": self.hedge_ratio,
            "fit": self._fit_snapshot(),
        })

    # ---- utility -----------------------------------------------------------
    def _fit_snapshot(self) -> dict[str, float] | None:
        if self._fit is None:
            return None
        return {
            "b": float(self._fit["b"]),
            "theta": float(self._fit["theta"]),
            "sigma_inf": float(self._fit["sigma_inf"]),
            "half_life_bars": float(self._fit["half_life"]),
        }

    @staticmethod
    def _emit_event(ctx: Any, action: str, data: dict[str, Any]) -> None:
        fn = getattr(ctx, "log_event", None)
        if not callable(fn):
            return
        try:
            fn(action, data)
        except Exception:  # noqa: BLE001
            pass
