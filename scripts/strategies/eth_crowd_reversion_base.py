"""Crowd-positioning reversion — shared base for the ETHUSDT 15m alpha suite.

Research story (see ``scripts/_alpha_lab/a5_edgescan.py`` ->
``a5_revtest.py`` -> ``a5_revfinal.py``):

    ETHUSDT 15m does NOT trend after costs.  An edge-existence scan (raw forward
    -return information-coefficient + decile spread + split-half stability) showed
    that *every* price-momentum signal had NEGATIVE forward IC, while several
    CROWD-POSITIONING signals had robust, split-half-stable reversion IC.  The
    alpha is fading crowding extremes on orthogonal perp-meta data sources:
    when one cohort gets abnormally one-sided, ETH tends to revert over ~1 day.

This base implements ONE leg of a 5-strategy portfolio.  Each concrete strategy
(funding / taker-flow / open-interest / top-trader account-LSR / top-trader
position-LSR) plugs a different ``SOURCE`` into the same machinery, and the five
legs are mutually uncorrelated (max pairwise monthly-return corr ~0.32).

Mechanism (single-position, fully causal, no look-ahead):
  * Each new 15m bar, sample the source's LAST-KNOWN value at the bar OPEN.
  * Standard sources: z-score the PRIOR bar's value over a trailing ``z_win``
    window (shifted, so only closed information is used).
        z > +z_thr  -> the crowd is extreme-long  -> fade SHORT
        z < -z_thr  -> the crowd is extreme-short -> fade LONG
  * Open-interest source: z-score the ``lb``-bar OI build-up; a fast OI rise
    (z > z_thr) plus a price move marks crowded fresh leverage that unwinds:
        OI up + price up   -> crowded longs  -> fade SHORT
        OI up + price down -> crowded shorts -> fade LONG
  * Entries are rising-edge (first bar of each signal run) and single-position.
  * Exit is a pure TIME exit after exactly ``max_hold_bars`` bars (the IC lives
    at a ~24-48h horizon; TP/SL truncates the edge).  An optional catastrophic
    ``sl_pct`` is available for live safety but was DISABLED during validation.

Data plumbing:
  * Backtest: source series are read from ``data/perp_meta/ETHUSDT_*.parquet``
    (the exact files the unified research dataset was built from), sampled with
    last-known-at-or-before semantics -> identical to the validated signal.
  * Live: funding / taker / open-interest are served by the production providers
    (``indicators.perp_meta_provider`` / ``indicators.oi_provider``).  The two
    TOP-TRADER LSR series (``count_toptrader_long_short_ratio`` and
    ``sum_toptrader_long_short_ratio``) are present in the LSR parquet but are
    NOT yet exposed by ``get_lsr_provider`` (which serves the global
    ``count_long_short_ratio``); deploying those two legs live requires either
    extending that provider or feeding the top-trader ratios from Redis.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from strategy.base import Strategy
from strategy.context import StrategyContext

def _find_perp_dir() -> Path:
    """Locate ``data/perp_meta`` robustly.

    Resolving via ``__file__`` alone breaks when this module is loaded from a
    temp/runtime location (e.g. the AlphaWeaver quick-backtest materialises the
    strategy code to a tmp file), so also honour the ``LLMTRADER_PERP_DIR`` env
    var and search upward from BOTH this file and the current working directory.
    """
    env = os.environ.get("LLMTRADER_PERP_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    seen: set[Path] = set()
    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for d in (start, *start.parents):
            cand = (d / "data" / "perp_meta").resolve()
            if cand in seen:
                continue
            seen.add(cand)
            if cand.is_dir():
                return cand
    return (Path(__file__).resolve().parents[2] / "data" / "perp_meta")


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PERP_DIR = _find_perp_dir()

# source-name -> (parquet suffix, timestamp column, value column, kind)
#   kind "std" : standard shifted-value reversion
#   kind "oi"  : open-interest build-up reversion (needs price confirmation)
SOURCE_SPEC: dict[str, tuple[str, str, str, str]] = {
    "funding":     ("funding",  "funding_time", "funding_rate",                    "std"),
    "taker":       ("taker_5m", "timestamp",    "sum_taker_long_short_vol_ratio",  "std"),
    "oi":          ("oi_5m",    "timestamp",    "sum_oi",                          "oi"),
    "lsr_top_acc": ("lsr_5m",   "timestamp",    "count_toptrader_long_short_ratio", "std"),
    "lsr_top_pos": ("lsr_5m",   "timestamp",    "sum_toptrader_long_short_ratio",  "std"),
}


def _make_parquet_sampler(symbol: str, source: str) -> Callable[[int], float]:
    """Return last_known(ts_ms): newest source value at or before ts_ms (NaN if
    none).  Mirrors the research dataset's no-look-ahead sampling exactly."""
    import pandas as pd

    suffix, ts_col, val_col, _kind = SOURCE_SPEC[source]
    path = _PERP_DIR / f"{symbol}_{suffix}.parquet"
    df = pd.read_parquet(path).sort_values(ts_col).reset_index(drop=True)
    ts = df[ts_col].to_numpy(dtype="int64")
    val = df[val_col].to_numpy(dtype="float64")

    def last_known(ts_ms: int) -> float:
        idx = int(np.searchsorted(ts, ts_ms, side="right")) - 1
        if idx < 0:
            return math.nan
        return float(val[idx])

    return last_known


def _make_live_sampler(symbol: str, source: str) -> Callable[[int], float] | None:
    """Try to bind a production provider for live mode.  Returns None when no
    matching provider exists (top-trader LSR) so the caller can fall back."""
    try:
        if source == "funding":
            from indicators.perp_meta_provider import get_funding_provider
            p = get_funding_provider(symbol)
        elif source == "taker":
            from indicators.perp_meta_provider import get_taker_provider
            p = get_taker_provider(symbol)
        elif source == "oi":
            from indicators.oi_provider import get_oi_provider
            p = get_oi_provider(symbol)
        else:
            return None  # top-trader LSR not exposed by get_lsr_provider
    except Exception:  # noqa: BLE001
        return None
    return lambda ts_ms: float(p.value_at(int(ts_ms)))


# ---------------------------------------------------------------------------
# Default params — concrete legs override SOURCE + these via their PRESET.
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = {
    "source": "funding",
    "z_win": 384,          # trailing window for the z-score (bars)
    "z_thr": 1.0,          # |z| threshold to call the crowd "extreme"
    "max_hold_bars": 192,  # pure time-exit horizon (bars); 192 = 48h on 15m
    "side": "long",        # "long" | "short" | "both"
    "lb": 96,              # OI build-up / price-confirm lookback (bars)
    "sl_pct": None,        # optional catastrophic stop (live safety); None = off
    "entry_pct": None,
}

STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {"name": "z_win", "type": "int", "min": 96, "max": 2880, "label": "Z-score window (bars)"},
    {"name": "z_thr", "type": "float", "min": 0.25, "max": 4.0, "step": 0.05, "label": "Z threshold"},
    {"name": "max_hold_bars", "type": "int", "min": 8, "max": 768, "label": "Time exit (bars)"},
    {"name": "side", "type": "str", "label": "Side (long/short/both)"},
    {"name": "lb", "type": "int", "min": 8, "max": 768, "label": "OI/price lookback (bars)"},
    {"name": "sl_pct", "type": "float", "min": 0.0, "max": 0.5, "step": 0.005, "label": "Catastrophic SL %"},
]


class CrowdReversionStrategy(Strategy):
    """Single-position crowd-positioning reversion with a pure time exit."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.source = str(p["source"])
        if self.source not in SOURCE_SPEC:
            raise ValueError(f"unknown source {self.source!r}")
        self.kind = SOURCE_SPEC[self.source][3]
        self.z_win = int(p["z_win"])
        self.z_thr = float(p["z_thr"])
        self.max_hold_bars = int(p["max_hold_bars"])
        self.side = str(p["side"])
        self.lb = int(p["lb"])
        self.sl_pct = None if p["sl_pct"] in (None, 0, 0.0) else float(p["sl_pct"])
        self.entry_pct = p["entry_pct"]

        self._mode: str | None = None
        self._sampler: Callable[[int], float] | None = None
        self._src: list[float] = []
        self._close: list[float] = []
        self._oichg: list[float] = []
        self._cap = max(self.z_win + self.lb + 8, self.lb + 8)

        self._bar_index = 0
        self._entry_bar_index: int | None = None
        self._entry_price: float | None = None
        self._is_closing = False
        self._prev_long = False
        self._prev_short = False

        self.params = dict(p)
        self.indicator_config = {}

    # ------------------------------------------------------------------ init
    def initialize(self, ctx: StrategyContext) -> None:
        ctx_cls = type(ctx).__name__
        ctx_module = type(ctx).__module__
        if "Backtest" in ctx_cls:
            mode = "backtest"
        elif ("Live" in ctx_cls or ctx_cls == "StreamBoundStrategyContext"
              or ctx_module.startswith("live.")):
            mode = "live"
        else:
            mode = None
        self._mode = mode
        symbol = getattr(ctx, "symbol", "ETHUSDT") or "ETHUSDT"

        sampler: Callable[[int], float] | None = None
        if mode == "live":
            sampler = _make_live_sampler(symbol, self.source)
        if sampler is None:
            try:
                sampler = _make_parquet_sampler(symbol, self.source)
            except Exception as exc:  # noqa: BLE001
                self._emit_event(ctx, "CROWDREV_DATA_ERROR",
                                 {"source": self.source, "error": repr(exc),
                                  "perp_dir": str(_PERP_DIR)})
                # Fail loudly instead of silently producing 0 trades: this
                # strategy CANNOT signal without its perp-meta source series.
                suffix = SOURCE_SPEC[self.source][0]
                raise RuntimeError(
                    f"crowd-reversion needs perp-meta data for source "
                    f"{self.source!r}: could not read "
                    f"{_PERP_DIR / f'{symbol}_{suffix}.parquet'} ({exc}). "
                    f"Set the LLMTRADER_PERP_DIR env var to the folder holding "
                    f"the {symbol}_*.parquet files, or run from a location where "
                    f"data/perp_meta exists."
                ) from exc
        self._sampler = sampler

        self._src, self._close, self._oichg = [], [], []
        self._bar_index = 0
        self._entry_bar_index = None
        self._entry_price = None
        self._is_closing = False
        self._prev_long = False
        self._prev_short = False

        self._emit_event(ctx, "CROWDREV_INIT", {
            "symbol": symbol, "mode": mode, "source": self.source,
            "kind": self.kind, "z_win": self.z_win, "z_thr": self.z_thr,
            "max_hold_bars": self.max_hold_bars, "side": self.side, "lb": self.lb,
            "live_provider": bool(mode == "live" and self.kind != "oi"
                                  and self.source in ("funding", "taker")),
        })

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _z(window: list[float], value: float) -> float:
        arr = np.asarray(window, dtype="float64")
        if not np.all(np.isfinite(arr)) or not math.isfinite(value):
            return math.nan
        mean = float(arr.mean())
        std = float(arr.std(ddof=0))
        if std <= 0:
            return math.nan
        return (value - mean) / std

    def _raw_signals(self) -> tuple[bool, bool]:
        """Level conditions (long_sig, short_sig) using only closed information.
        No edge / state mutation -- evaluated EVERY bar so the rising-edge state
        stays continuous exactly like the validated research signal."""
        if self.kind == "oi":
            if len(self._oichg) < self.z_win or len(self._close) < self.lb + 1:
                return False, False
            window = self._oichg[-self.z_win:]
            z = self._z(window, window[-1])
            if not math.isfinite(z):
                return False, False
            denom = self._close[-1 - self.lb]
            if not math.isfinite(denom) or denom == 0:
                return False, False
            pr = self._close[-1] / denom - 1.0
            long_sig = (z > self.z_thr) and (pr < 0)
            short_sig = (z > self.z_thr) and (pr > 0)
        else:
            if len(self._src) < self.z_win + 1:
                return False, False
            value = self._src[-2]              # shift(1): prior bar's value
            window = self._src[-(self.z_win + 1):-1]
            z = self._z(window, value)
            if not math.isfinite(z):
                return False, False
            long_sig = z < -self.z_thr
            short_sig = z > self.z_thr
        if self.side == "long":
            short_sig = False
        elif self.side == "short":
            long_sig = False
        return bool(long_sig), bool(short_sig)

    # ------------------------------------------------------------------ bar
    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self._is_closing = False
            self._entry_bar_index = None
            self._entry_price = None

        if not bool(bar.get("is_new_bar", True)):
            return

        close = float(bar.get("close", bar.get("price", 0.0)) or 0.0)
        if not math.isfinite(close) or close <= 0:
            return
        open_ = float(bar.get("open", close) or close)
        low = float(bar.get("low", close) or close)
        high = float(bar.get("high", close) or close)
        ts_open = int(bar.get("bar_timestamp", bar.get("timestamp", 0)) or 0)

        # ---- sample the source at this bar's open (last-known, no look-ahead)
        s = self._sampler(ts_open) if self._sampler is not None else math.nan
        self._src.append(s)
        self._close.append(close)
        if self.kind == "oi":
            if len(self._src) >= self.lb + 2:
                base = self._src[-2 - self.lb]
                cur = self._src[-2]
                oc = (cur / base - 1.0) if (math.isfinite(base) and base != 0
                                            and math.isfinite(cur)) else math.nan
            else:
                oc = math.nan
            self._oichg.append(oc)
            if len(self._oichg) > self._cap:
                self._oichg = self._oichg[-self._cap:]
        if len(self._src) > self._cap:
            self._src = self._src[-self._cap:]
        if len(self._close) > self._cap:
            self._close = self._close[-self._cap:]

        # ---- per-bar counter + CONTINUOUS rising-edge evaluation -----------
        # Evaluated every bar (even while holding) so edge state matches the
        # research _edge over the uninterrupted signal series.
        self._bar_index += 1
        long_sig, short_sig = self._raw_signals()
        long_edge = long_sig and not self._prev_long
        short_edge = short_sig and not self._prev_short
        self._prev_long = long_sig
        self._prev_short = short_sig
        desired = 1 if long_edge else (-1 if short_edge else 0)

        # ---- exits: optional catastrophic SL, then pure time exit ----------
        if (ctx.position_size != 0 and self._entry_price is not None
                and not self._is_closing):
            if self.sl_pct is not None:
                if ctx.position_size > 0:
                    sl_level = self._entry_price * (1.0 - self.sl_pct)
                    if low <= sl_level:
                        self._close_at(ctx, min(open_, sl_level) if open_ <= sl_level else sl_level,
                                       "CROWDREV: SL")
                        return
                else:
                    sl_level = self._entry_price * (1.0 + self.sl_pct)
                    if high >= sl_level:
                        self._close_at(ctx, max(open_, sl_level) if open_ >= sl_level else sl_level,
                                       "CROWDREV: SL")
                        return
            if (self._entry_bar_index is not None
                    and self._bar_index - self._entry_bar_index >= self.max_hold_bars):
                self._is_closing = True
                ctx.close_position(reason=f"CROWDREV: time exit ({self.max_hold_bars} bars)")
                self._emit_event(ctx, "CROWDREV_EXIT_TIME", {
                    "source": self.source, "entry_price": self._entry_price,
                    "exit_price": close,
                    "held_bars": self._bar_index - self._entry_bar_index})
                return

        # ---- entry: flat, no open orders, fresh rising edge ----------------
        try:
            open_orders = ctx.get_open_orders() or []
        except Exception:  # noqa: BLE001
            open_orders = []
        if open_orders:
            return

        if ctx.position_size != 0:
            return

        direction = desired
        if direction == 0:
            return
        if direction > 0:
            reason = f"CrowdRev long [{self.source}] fade short-crowd"
            if self.entry_pct is None:
                ctx.enter_long(reason=reason)
            else:
                ctx.enter_long(reason=reason, entry_pct=float(self.entry_pct))
            self._emit_event(ctx, "CROWDREV_ENTRY_LONG",
                             {"source": self.source, "entry_price": close})
        else:
            reason = f"CrowdRev short [{self.source}] fade long-crowd"
            if self.entry_pct is None:
                ctx.enter_short(reason=reason)
            else:
                ctx.enter_short(reason=reason, entry_pct=float(self.entry_pct))
            self._emit_event(ctx, "CROWDREV_ENTRY_SHORT",
                             {"source": self.source, "entry_price": close})
        self._entry_bar_index = self._bar_index
        self._entry_price = close

    # ---- helpers -----------------------------------------------------------
    def _close_at(self, ctx: Any, price: float, reason: str) -> None:
        self._is_closing = True
        if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
            ctx.close_position_at_price(price, reason=reason)
        else:
            ctx.close_position(reason=reason)
        self._emit_event(ctx, "CROWDREV_EXIT_SL",
                         {"source": self.source, "entry_price": self._entry_price,
                          "exit_price": price})

    def _emit_event(self, ctx: Any, action: str, data: dict[str, Any]) -> None:
        fn = getattr(ctx, "log_event", None)
        if not callable(fn):
            return
        try:
            fn(action, data)
        except Exception:  # noqa: BLE001
            pass
