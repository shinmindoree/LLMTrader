"""17-leg multi-factor portfolio strategy for BTCUSDT-PERP.

Discovered by the alpha-lab Pass-5 sweep + trend mini-sweep + portfolio combiner.
See [docs/multi-factor-portfolio-alpha.md] for the full discovery report.

==============================================================================
DATA RESOLUTION (cloud / local)
==============================================================================
The strategy needs 5 parquet files (15m klines + OI + funding + taker + LSR).
Files are resolved per-kind in this order, mirroring the OI provider pattern:

  1. Explicit env path:    MFP_PARQUET_PATH_<KIND>_<SYMBOL>
  2. Local file:           data/perp_meta/<SYMBOL>_<file>.parquet
  3. HTTP URL fallback:    MFP_PARQUET_URL_<KIND>_<SYMBOL>          (per-kind)
                           or MFP_PARQUET_BASE_URL                  (joined w/ filename)
  4. Azure blob fallback:  MFP_PARQUET_BLOB_CONTAINER + MFP_PARQUET_BLOB_NAME_<KIND>_<SYMBOL>
                           or MFP_PARQUET_BLOB_PREFIX               (joined w/ filename)
  5. Otherwise: RuntimeError with the list of attempted sources.

KIND values: KLINES, OI, FUNDING, TAKER, LSR.

Downloaded files are cached at MFP_PARQUET_CACHE_DIR (default /tmp/mfp_parquet)
to avoid re-downloading on subsequent backtests.
==============================================================================

Composition (equal-weight):
  - 15 mean-reversion legs:
      pullback_in_trend × 1   (30m)
      oi_z_combo        × 4   (30m, 60m)
      lsr_taker_confluence × 5 (15m, 60m)
      ensemble_meanrev  × 5   (15m, 60m)
  - 2 trend-follow legs:
      donchian_breakout × 2   (60m, 240m)

Conservative SL fill semantics (matches the vectorised lab):
  LONG  SL: open<=sl_level → fill at open (gap-down loss);
            elif low<=sl_level → fill at sl_level (touch).
  SHORT SL: open>=sl_level → fill at open;
            elif high>=sl_level → fill at sl_level.
  TP:  fills exactly at tp_level (limit-style).
  TIME exit: fills at bar close (with optional slippage).
  SL is evaluated BEFORE TP within a bar (pessimistic ordering).

TEST window 2025-01-01 → 2026-04-29 metrics:
  TPD 2.75, return +18.94%, MDD 1.66%, +months 88% (14/16),
  worst month -0.20%, sharpe 3.80, calmar 8.56.

==============================================================================
RUNTIME CONTRACT
==============================================================================
- Base candle interval: **15m**. The runner must feed 15m bars; this strategy
  internally resamples to 30m / 60m / 240m for the higher-TF legs.
- Required parquet files at <repo>/data/perp_meta/:
    BTCUSDT_15m_klines.parquet
    BTCUSDT_oi_5m.parquet
    BTCUSDT_funding.parquet
    BTCUSDT_taker_5m.parquet
    BTCUSDT_lsr_5m.parquet
- Net position semantics:
  Each new bar each leg outputs side ∈ {-1, 0, +1}. The strategy commands the
  ctx into the **majority direction** (long if Σside > 0, short if < 0, flat
  if 0). Per-leg SL/TP/TIME exits update internal leg state and may flip the
  net direction. Sizing is full-notional (`enter_long`/`enter_short` without
  `entry_pct`). For true 1/17-each notional weighting deploy 17 separate
  runner jobs with `entry_pct = 100/17 ≈ 5.88` and run one leg per job.
- LIVE mode: this strategy runs in BACKTEST mode only. Funding/taker/LSR
  data has no live provider yet; running live will raise NotImplementedError.

==============================================================================
"""
from __future__ import annotations

import logging
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import talib

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from strategy.base import Strategy
from strategy.context import StrategyContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 17-leg portfolio config (frozen from alpha_lab_portfolio_v2.json)
# ---------------------------------------------------------------------------
_MEAN_REV_LEGS: list[dict[str, Any]] = [
    {"family": "pullback_in_trend",
     "config": {"interval_min": 30, "htf_ema_period": 100, "pullback_rsi_period": 7,
                "rsi_long": 25.0, "rsi_short": 75.0, "bb_period": 20, "bb_std": 2.0,
                "use_bb": True, "use_atr_floor": True, "atr_min_pct": 0.0025,
                "atr_max_pct": 0.025, "use_funding": False,
                "tp_pct": 0.020, "sl_pct": 0.012, "max_hold_h": 16, "side": "both"}},
    {"family": "oi_z_combo",
     "config": {"interval_min": 60, "oi_lb": 192, "z_lookback": 480,
                "z_long": -1.5, "z_short": 1.5, "use_rsi": True,
                "rsi_long_max": 45.0, "rsi_short_min": 65.0, "use_taker": False,
                "taker_long_max": 0.95, "taker_short_min": 1.05,
                "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025,
                "tp_pct": 0.018, "sl_pct": 0.012, "max_hold_h": 16, "side": "both"}},
    {"family": "oi_z_combo",
     "config": {"interval_min": 60, "oi_lb": 192, "z_lookback": 480,
                "z_long": -1.5, "z_short": 2.0, "use_rsi": True,
                "rsi_long_max": 45.0, "rsi_short_min": 55.0, "use_taker": False,
                "taker_long_max": 0.95, "taker_short_min": 1.05,
                "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025,
                "tp_pct": 0.025, "sl_pct": 0.012, "max_hold_h": 8, "side": "both"}},
    {"family": "oi_z_combo",
     "config": {"interval_min": 60, "oi_lb": 192, "z_lookback": 480,
                "z_long": -1.5, "z_short": 1.5, "use_rsi": True,
                "rsi_long_max": 45.0, "rsi_short_min": 65.0, "use_taker": False,
                "taker_long_max": 0.95, "taker_short_min": 1.05,
                "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025,
                "tp_pct": 0.025, "sl_pct": 0.012, "max_hold_h": 16, "side": "both"}},
    {"family": "oi_z_combo",
     "config": {"interval_min": 30, "oi_lb": 48, "z_lookback": 240,
                "z_long": -2.0, "z_short": 2.0, "use_rsi": True,
                "rsi_long_max": 45.0, "rsi_short_min": 65.0, "use_taker": False,
                "taker_long_max": 0.95, "taker_short_min": 1.05,
                "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025,
                "tp_pct": 0.025, "sl_pct": 0.012, "max_hold_h": 16, "side": "both"}},
    {"family": "lsr_taker_confluence",
     "config": {"interval_min": 15, "lsr_lb": 240, "z_lsr_long": -1.5,
                "z_lsr_short": 2.0, "use_taker": False, "taker_lb": 240,
                "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count",
                "use_rsi": True, "rsi_long": 35.0, "rsi_short": 65.0,
                "tp_pct": 0.018, "sl_pct": 0.012, "max_hold_h": 8, "side": "both"}},
    {"family": "lsr_taker_confluence",
     "config": {"interval_min": 15, "lsr_lb": 240, "z_lsr_long": -1.0,
                "z_lsr_short": 1.5, "use_taker": True, "taker_lb": 240,
                "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count",
                "use_rsi": True, "rsi_long": 35.0, "rsi_short": 60.0,
                "tp_pct": 0.018, "sl_pct": 0.008, "max_hold_h": 16, "side": "both"}},
    {"family": "lsr_taker_confluence",
     "config": {"interval_min": 60, "lsr_lb": 240, "z_lsr_long": -1.0,
                "z_lsr_short": 1.0, "use_taker": False, "taker_lb": 240,
                "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count",
                "use_rsi": True, "rsi_long": 40.0, "rsi_short": 65.0,
                "tp_pct": 0.025, "sl_pct": 0.012, "max_hold_h": 8, "side": "both"}},
    {"family": "lsr_taker_confluence",
     "config": {"interval_min": 15, "lsr_lb": 240, "z_lsr_long": -1.5,
                "z_lsr_short": 2.0, "use_taker": False, "taker_lb": 240,
                "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count",
                "use_rsi": True, "rsi_long": 35.0, "rsi_short": 65.0,
                "tp_pct": 0.025, "sl_pct": 0.008, "max_hold_h": 8, "side": "both"}},
    {"family": "lsr_taker_confluence",
     "config": {"interval_min": 15, "lsr_lb": 240, "z_lsr_long": -1.0,
                "z_lsr_short": 1.5, "use_taker": True, "taker_lb": 240,
                "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count",
                "use_rsi": True, "rsi_long": 35.0, "rsi_short": 60.0,
                "tp_pct": 0.012, "sl_pct": 0.012, "max_hold_h": 16, "side": "both"}},
    {"family": "ensemble_meanrev",
     "config": {"interval_min": 60, "bb_period": 20, "bb_std": 2.0,
                "rsi_period": 14, "rsi_long": 40.0, "rsi_short": 60.0,
                "oi_lb": 96, "oi_drop": -0.015, "oi_pop": 0.020,
                "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07,
                "lsr_z_lookback": 480, "lsr_z_long": -1.0, "lsr_z_short": 1.5,
                "min_votes": 3, "use_trend_filter": True, "trend_ema": 200,
                "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025,
                "tp_pct": 0.012, "sl_pct": 0.012, "max_hold_h": 24, "side": "both"}},
    {"family": "ensemble_meanrev",
     "config": {"interval_min": 60, "bb_period": 20, "bb_std": 2.0,
                "rsi_period": 14, "rsi_long": 35.0, "rsi_short": 65.0,
                "oi_lb": 96, "oi_drop": -0.025, "oi_pop": 0.030,
                "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07,
                "lsr_z_lookback": 480, "lsr_z_long": -1.0, "lsr_z_short": 1.0,
                "min_votes": 3, "use_trend_filter": True, "trend_ema": 200,
                "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025,
                "tp_pct": 0.012, "sl_pct": 0.012, "max_hold_h": 16, "side": "both"}},
    {"family": "ensemble_meanrev",
     "config": {"interval_min": 60, "bb_period": 20, "bb_std": 1.8,
                "rsi_period": 14, "rsi_long": 40.0, "rsi_short": 65.0,
                "oi_lb": 96, "oi_drop": -0.015, "oi_pop": 0.020,
                "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07,
                "lsr_z_lookback": 480, "lsr_z_long": -1.0, "lsr_z_short": 1.5,
                "min_votes": 3, "use_trend_filter": True, "trend_ema": 200,
                "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025,
                "tp_pct": 0.012, "sl_pct": 0.012, "max_hold_h": 8, "side": "both"}},
    {"family": "ensemble_meanrev",
     "config": {"interval_min": 15, "bb_period": 20, "bb_std": 2.0,
                "rsi_period": 14, "rsi_long": 40.0, "rsi_short": 70.0,
                "oi_lb": 96, "oi_drop": -0.025, "oi_pop": 0.030,
                "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07,
                "lsr_z_lookback": 480, "lsr_z_long": -1.5, "lsr_z_short": 1.0,
                "min_votes": 3, "use_trend_filter": True, "trend_ema": 200,
                "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025,
                "tp_pct": 0.025, "sl_pct": 0.012, "max_hold_h": 24, "side": "both"}},
    {"family": "ensemble_meanrev",
     "config": {"interval_min": 15, "bb_period": 20, "bb_std": 2.0,
                "rsi_period": 14, "rsi_long": 30.0, "rsi_short": 70.0,
                "oi_lb": 96, "oi_drop": -0.025, "oi_pop": 0.030,
                "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07,
                "lsr_z_lookback": 480, "lsr_z_long": -1.5, "lsr_z_short": 1.0,
                "min_votes": 3, "use_trend_filter": True, "trend_ema": 200,
                "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025,
                "tp_pct": 0.025, "sl_pct": 0.008, "max_hold_h": 24, "side": "both"}},
]

_TREND_LEGS: list[dict[str, Any]] = [
    {"family": "donchian_breakout",
     "config": {"interval_min": 60, "dc_period": 48, "atr_min_mult": 0.0,
                "use_oi": False, "oi_lb": 96, "oi_min_for_long": 0.0,
                "oi_max_for_short": -0.01, "tp_pct": 0.08, "sl_pct": 0.012,
                "max_hold_h": 48, "side": "both"}},
    {"family": "donchian_breakout",
     "config": {"interval_min": 240, "dc_period": 192, "atr_min_mult": 0.0,
                "use_oi": False, "oi_lb": 96, "oi_min_for_long": 0.0,
                "oi_max_for_short": -0.01, "tp_pct": 0.08, "sl_pct": 0.012,
                "max_hold_h": 96, "side": "both"}},
]

ALL_LEGS: list[dict[str, Any]] = _MEAN_REV_LEGS + _TREND_LEGS  # 17 legs total


# ---------------------------------------------------------------------------
# Vectorised helpers (mirror scripts/_alpha_lab/strategies.py)
# ---------------------------------------------------------------------------
def _zscore(x: np.ndarray, lookback: int) -> np.ndarray:
    s = pd.Series(x)
    mu = s.rolling(lookback, min_periods=max(8, lookback // 4)).mean()
    sd = s.rolling(lookback, min_periods=max(8, lookback // 4)).std(ddof=0)
    z = (s - mu) / sd.replace(0.0, np.nan)
    return z.to_numpy(dtype="float64")


def _pct_change_n(x: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(x)
    return (s / s.shift(n) - 1.0).to_numpy(dtype="float64")


def _atr_pct(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    h = df["high"].to_numpy(dtype="float64")
    lo = df["low"].to_numpy(dtype="float64")
    c = df["close"].to_numpy(dtype="float64")
    atr = talib.ATR(h, lo, c, timeperiod=period)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(c > 0, atr / c, np.nan)


def _edge(sig: np.ndarray) -> np.ndarray:
    sig = np.asarray(sig, dtype=bool)
    if not sig.size:
        return sig
    out = np.zeros_like(sig)
    out[0] = sig[0]
    out[1:] = sig[1:] & ~sig[:-1]
    return out


# ---------------------------------------------------------------------------
# Per-family signal generators (vectorised at the leg's TF)
# ---------------------------------------------------------------------------
def _sig_pullback_in_trend(df: pd.DataFrame, c: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    cl = df["close"].to_numpy(dtype="float64")
    ema = talib.EMA(cl, timeperiod=int(c["htf_ema_period"]))
    rsi = talib.RSI(cl, timeperiod=int(c["pullback_rsi_period"]))
    long_sig = (cl > ema) & (rsi <= float(c["rsi_long"]))
    short_sig = (cl < ema) & (rsi >= float(c["rsi_short"]))
    if c.get("use_bb", True):
        upper, _mid, lower = talib.BBANDS(
            cl, timeperiod=int(c["bb_period"]),
            nbdevup=float(c["bb_std"]), nbdevdn=float(c["bb_std"]),
        )
        long_sig = long_sig & (cl <= lower)
        short_sig = short_sig & (cl >= upper)
    if c.get("use_atr_floor", True):
        atrp = _atr_pct(df, period=14)
        ok = (atrp >= float(c["atr_min_pct"])) & (atrp <= float(c["atr_max_pct"]))
        long_sig = long_sig & ok
        short_sig = short_sig & ok
    if c.get("use_funding", False):
        fr = df["funding_rate"].to_numpy(dtype="float64")
        long_sig = long_sig & (fr <= float(c.get("fund_max_for_long", 0.0005)))
        short_sig = short_sig & (fr >= float(c.get("fund_min_for_short", -0.0005)))
    return _apply_side(long_sig, short_sig, c.get("side", "both"))


def _sig_oi_z_combo(df: pd.DataFrame, c: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    oi = df["oi"].to_numpy(dtype="float64")
    cl = df["close"].to_numpy(dtype="float64")
    oi_pct = _pct_change_n(oi, int(c["oi_lb"]))
    z = _zscore(oi_pct, int(c["z_lookback"]))
    long_sig = z <= float(c["z_long"])
    short_sig = z >= float(c["z_short"])
    if c.get("use_rsi", True):
        rsi = talib.RSI(cl, timeperiod=14)
        long_sig = long_sig & (rsi <= float(c["rsi_long_max"]))
        short_sig = short_sig & (rsi >= float(c["rsi_short_min"]))
    if c.get("use_taker", False):
        tr = pd.Series(df["taker_ratio"].to_numpy(dtype="float64"))
        smooth = int(c.get("taker_smooth", 4))
        if smooth > 1:
            tr = tr.rolling(smooth, min_periods=max(1, smooth // 2)).mean()
        tr_arr = tr.to_numpy(dtype="float64")
        long_sig = long_sig & (tr_arr <= float(c["taker_long_max"]))
        short_sig = short_sig & (tr_arr >= float(c["taker_short_min"]))
    if c.get("use_atr_filter", True):
        atrp = _atr_pct(df, period=14)
        ok = (atrp >= float(c["atr_min_pct"])) & (atrp <= float(c["atr_max_pct"]))
        long_sig = long_sig & ok
        short_sig = short_sig & ok
    return _apply_side(long_sig, short_sig, c.get("side", "both"))


def _sig_lsr_taker_confluence(df: pd.DataFrame, c: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    lsr_col = str(c.get("lsr_col", "lsr_count"))
    lsr = df[lsr_col].to_numpy(dtype="float64")
    z_lsr = _zscore(lsr, int(c["lsr_lb"]))
    long_sig = z_lsr <= float(c["z_lsr_long"])
    short_sig = z_lsr >= float(c["z_lsr_short"])
    if c.get("use_taker", True):
        tr = df["taker_ratio"].to_numpy(dtype="float64")
        z_tr = _zscore(tr, int(c["taker_lb"]))
        long_sig = long_sig & (z_tr <= float(c["z_taker_long"]))
        short_sig = short_sig & (z_tr >= float(c["z_taker_short"]))
    if c.get("use_rsi", True):
        cl = df["close"].to_numpy(dtype="float64")
        rsi = talib.RSI(cl, timeperiod=14)
        long_sig = long_sig & (rsi <= float(c["rsi_long"]))
        short_sig = short_sig & (rsi >= float(c["rsi_short"]))
    return _apply_side(long_sig, short_sig, c.get("side", "both"))


def _sig_ensemble_meanrev(df: pd.DataFrame, c: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    cl = df["close"].to_numpy(dtype="float64")
    upper, _mid, lower = talib.BBANDS(
        cl, timeperiod=int(c["bb_period"]),
        nbdevup=float(c["bb_std"]), nbdevdn=float(c["bb_std"]),
    )
    rsi = talib.RSI(cl, timeperiod=int(c["rsi_period"]))
    bb_long = (cl <= lower) & (rsi <= float(c["rsi_long"]))
    bb_short = (cl >= upper) & (rsi >= float(c["rsi_short"]))

    oi_pct = _pct_change_n(df["oi"].to_numpy(dtype="float64"), int(c["oi_lb"]))
    px_pct = _pct_change_n(cl, int(c["oi_lb"]))
    oi_long = (oi_pct <= float(c["oi_drop"])) & (px_pct < 0)
    oi_short = (oi_pct >= float(c["oi_pop"])) & (px_pct > 0)

    tr_arr = df["taker_ratio"].to_numpy(dtype="float64")
    tr_smoothed = pd.Series(tr_arr).rolling(
        int(c["taker_lb"]), min_periods=max(1, int(c["taker_lb"]) // 4)
    ).mean().to_numpy(dtype="float64")
    tk_long = tr_smoothed <= float(c["taker_long_max"])
    tk_short = tr_smoothed >= float(c["taker_short_min"])

    lsr_arr = df["lsr_count"].to_numpy(dtype="float64")
    z_lsr = _zscore(lsr_arr, int(c["lsr_z_lookback"]))
    lsr_long = z_lsr <= float(c["lsr_z_long"])
    lsr_short = z_lsr >= float(c["lsr_z_short"])

    long_votes = (bb_long.astype("int8") + oi_long.astype("int8")
                  + tk_long.astype("int8") + lsr_long.astype("int8"))
    short_votes = (bb_short.astype("int8") + oi_short.astype("int8")
                   + tk_short.astype("int8") + lsr_short.astype("int8"))
    long_sig = long_votes >= int(c["min_votes"])
    short_sig = short_votes >= int(c["min_votes"])

    if c.get("use_trend_filter", True):
        ema = talib.EMA(cl, timeperiod=int(c["trend_ema"]))
        long_sig = long_sig & (cl >= ema * 0.98)
        short_sig = short_sig & (cl <= ema * 1.02)
    if c.get("use_atr_filter", True):
        atrp = _atr_pct(df, period=14)
        ok = (atrp >= float(c["atr_min_pct"])) & (atrp <= float(c["atr_max_pct"]))
        long_sig = long_sig & ok
        short_sig = short_sig & ok
    return _apply_side(long_sig, short_sig, c.get("side", "both"))


def _sig_donchian_breakout(df: pd.DataFrame, c: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    h = df["high"].to_numpy(dtype="float64")
    lo = df["low"].to_numpy(dtype="float64")
    cl = df["close"].to_numpy(dtype="float64")
    s_h = pd.Series(h)
    s_lo = pd.Series(lo)
    dc = int(c["dc_period"])
    upper = s_h.shift(1).rolling(dc, min_periods=dc).max().to_numpy("float64")
    lower = s_lo.shift(1).rolling(dc, min_periods=dc).min().to_numpy("float64")
    long_sig = cl > upper
    short_sig = cl < lower
    atr_min_mult = float(c.get("atr_min_mult", 0.0))
    if atr_min_mult > 0:
        atr = talib.ATR(h, lo, cl, timeperiod=14)
        atr_med = pd.Series(atr).rolling(56, min_periods=14).median().to_numpy()
        ok = atr >= atr_min_mult * atr_med
        long_sig = long_sig & ok
        short_sig = short_sig & ok
    if c.get("use_oi", False):
        oi_pct = _pct_change_n(df["oi"].to_numpy(dtype="float64"), int(c["oi_lb"]))
        long_sig = long_sig & (oi_pct >= float(c["oi_min_for_long"]))
        short_sig = short_sig & (oi_pct <= float(c["oi_max_for_short"]))
    return _apply_side(long_sig, short_sig, c.get("side", "both"))


def _apply_side(long_sig: np.ndarray, short_sig: np.ndarray,
                side: str) -> tuple[np.ndarray, np.ndarray]:
    long_sig = np.asarray(long_sig, dtype=bool)
    short_sig = np.asarray(short_sig, dtype=bool)
    if side == "long_only":
        short_sig = np.zeros_like(short_sig)
    elif side == "short_only":
        long_sig = np.zeros_like(long_sig)
    return _edge(long_sig), _edge(short_sig)


_SIG_FUNCS: dict[str, Any] = {
    "pullback_in_trend": _sig_pullback_in_trend,
    "oi_z_combo": _sig_oi_z_combo,
    "lsr_taker_confluence": _sig_lsr_taker_confluence,
    "ensemble_meanrev": _sig_ensemble_meanrev,
    "donchian_breakout": _sig_donchian_breakout,
}


# ---------------------------------------------------------------------------
# Dataset loader (mirrors scripts/_alpha_lab/dataset.py)
# ---------------------------------------------------------------------------
_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "perp_meta"

# kind -> filename suffix template ("{symbol}_<...>.parquet")
_PARQUET_KINDS: dict[str, str] = {
    "KLINES":  "{symbol}_15m_klines.parquet",
    "OI":      "{symbol}_oi_5m.parquet",
    "FUNDING": "{symbol}_funding.parquet",
    "TAKER":   "{symbol}_taker_5m.parquet",
    "LSR":     "{symbol}_lsr_5m.parquet",
}


def _cache_dir() -> Path:
    p = Path(os.environ.get("MFP_PARQUET_CACHE_DIR", "/tmp/mfp_parquet"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download_url_to(url: str, dest: Path) -> None:
    import httpx  # local import: only needed when env URL is configured
    logger.info("[mfp] downloading %s -> %s", url, dest)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 16):
                f.write(chunk)
    tmp.replace(dest)


def _download_blob_to(container_name: str, blob_name: str, dest: Path) -> None:
    """Download an Azure blob using managed identity / connection string."""
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    from azure.storage.blob import ContainerClient
    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "").strip()
    if conn_str:
        client = ContainerClient.from_connection_string(conn_str, container_name)
    else:
        account_url = os.environ.get("AZURE_BLOB_ACCOUNT_URL", "").strip()
        if not account_url:
            raise RuntimeError(
                "MFP_PARQUET_BLOB_* set but no AZURE_BLOB_CONNECTION_STRING / "
                "AZURE_BLOB_ACCOUNT_URL configured."
            )
        client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
        if os.environ.get("IDENTITY_ENDPOINT"):
            kwargs: dict[str, Any] = {}
            if client_id:
                kwargs["client_id"] = client_id
            credential = ManagedIdentityCredential(**kwargs)
        else:
            kwargs = {}
            if client_id:
                kwargs["managed_identity_client_id"] = client_id
            credential = DefaultAzureCredential(**kwargs)
        client = ContainerClient(account_url=account_url,
                                 container_name=container_name,
                                 credential=credential)
    logger.info("[mfp] downloading blob %s/%s -> %s", container_name, blob_name, dest)
    data = client.download_blob(blob_name).readall()
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)


def _resolve_parquet(symbol: str, kind: str) -> Path:
    """Resolve a parquet file for (symbol, kind) using local + URL + blob fallbacks."""
    sym = symbol.upper()
    template = _PARQUET_KINDS[kind]
    filename = template.format(symbol=sym)
    attempted: list[str] = []

    # 1) Explicit env path
    env_path = os.environ.get(f"MFP_PARQUET_PATH_{kind}_{sym}", "").strip()
    if env_path:
        p = Path(env_path)
        attempted.append(f"env path {p}")
        if p.exists():
            return p

    # 2) Local file
    local = _DATA_DIR / filename
    attempted.append(f"local {local}")
    if local.exists():
        return local

    # 3) HTTP URL (per-kind, then base url + filename)
    url = os.environ.get(f"MFP_PARQUET_URL_{kind}_{sym}", "").strip()
    if not url:
        base = os.environ.get("MFP_PARQUET_BASE_URL", "").strip()
        if base:
            url = base.rstrip("/") + "/" + filename
    if url:
        attempted.append(f"url {url}")
        cache = _cache_dir() / filename
        if not cache.exists():
            _download_url_to(url, cache)
        return cache

    # 4) Azure blob (per-kind name, or prefix + filename)
    container_name = os.environ.get("MFP_PARQUET_BLOB_CONTAINER", "").strip()
    if container_name:
        blob_name = os.environ.get(f"MFP_PARQUET_BLOB_NAME_{kind}_{sym}", "").strip()
        if not blob_name:
            prefix = os.environ.get("MFP_PARQUET_BLOB_PREFIX", "").strip()
            if prefix:
                blob_name = prefix.rstrip("/") + "/" + filename
            else:
                blob_name = filename
        attempted.append(f"blob {container_name}/{blob_name}")
        cache = _cache_dir() / filename
        if not cache.exists():
            _download_blob_to(container_name, blob_name, cache)
        return cache

    raise RuntimeError(
        f"MultiFactorPortfolioStrategy could not locate {kind} parquet for {sym}. "
        f"Tried: {attempted}. "
        f"Configure one of: MFP_PARQUET_PATH_{kind}_{sym}, "
        f"file at {local}, MFP_PARQUET_URL_{kind}_{sym} (or MFP_PARQUET_BASE_URL), "
        f"or MFP_PARQUET_BLOB_CONTAINER + MFP_PARQUET_BLOB_NAME_{kind}_{sym} "
        f"(or MFP_PARQUET_BLOB_PREFIX)."
    )


def _last_known(times: np.ndarray, values: np.ndarray,
                sample_times: np.ndarray) -> np.ndarray:
    idx = np.searchsorted(times, sample_times, side="right") - 1
    out = np.empty_like(sample_times, dtype="float64")
    valid = idx >= 0
    out[valid] = values[idx[valid]]
    out[~valid] = np.nan
    return out


def _load_unified_dataset(symbol: str = "BTCUSDT") -> pd.DataFrame:
    klines = pd.read_parquet(_resolve_parquet(symbol, "KLINES"))
    klines = klines.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    klines = klines.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
    ts = klines["ts"].to_numpy(dtype="int64")
    df = pd.DataFrame({
        "ts": ts,
        "open": klines["open"].to_numpy(dtype="float64"),
        "high": klines["high"].to_numpy(dtype="float64"),
        "low": klines["low"].to_numpy(dtype="float64"),
        "close": klines["close"].to_numpy(dtype="float64"),
    })
    oi = pd.read_parquet(_resolve_parquet(symbol, "OI")).sort_values("timestamp")
    df["oi"] = _last_known(
        oi["timestamp"].to_numpy(dtype="int64"),
        oi["sum_oi"].to_numpy(dtype="float64"),
        ts,
    )
    fund = pd.read_parquet(_resolve_parquet(symbol, "FUNDING")).sort_values("funding_time")
    df["funding_rate"] = _last_known(
        fund["funding_time"].to_numpy(dtype="int64"),
        fund["funding_rate"].to_numpy(dtype="float64"),
        ts,
    )
    taker = pd.read_parquet(_resolve_parquet(symbol, "TAKER")).sort_values("timestamp")
    df["taker_ratio"] = _last_known(
        taker["timestamp"].to_numpy(dtype="int64"),
        taker["sum_taker_long_short_vol_ratio"].to_numpy(dtype="float64"),
        ts,
    )
    lsr = pd.read_parquet(_resolve_parquet(symbol, "LSR")).sort_values("timestamp")
    df["lsr_count"] = _last_known(
        lsr["timestamp"].to_numpy(dtype="int64"),
        lsr["count_long_short_ratio"].to_numpy(dtype="float64"),
        ts,
    )
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def _resample_to(df: pd.DataFrame, target_min: int) -> pd.DataFrame:
    if target_min == 15:
        return df.copy()
    if target_min % 15 != 0 or target_min < 15:
        raise ValueError(f"target_min must be a multiple of 15: got {target_min}")
    rule = f"{target_min}min"
    work = df.copy().set_index("dt")
    aux_cols = [c for c in work.columns if c not in ("ts", "open", "high", "low", "close", "dt")]
    agg: dict[str, Any] = {"open": "first", "high": "max", "low": "min", "close": "last", "ts": "first"}
    for c in aux_cols:
        agg[c] = "last"
    out = work.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open", "close"])
    return out.reset_index(drop=False)


# ---------------------------------------------------------------------------
# Per-leg runtime state
# ---------------------------------------------------------------------------
class _LegState:
    __slots__ = (
        "family", "config", "interval_min", "tf_ts", "tf_open", "tf_high", "tf_low", "tf_close",
        "long_sig", "short_sig", "side", "entry_price", "entry_tf_idx", "tp_pct", "sl_pct",
        "max_hold_bars",
    )

    def __init__(self, leg: dict[str, Any], unified: pd.DataFrame) -> None:
        self.family = leg["family"]
        self.config = dict(leg["config"])
        self.interval_min = int(self.config["interval_min"])
        # Resample once at init.
        tf_df = _resample_to(unified, self.interval_min)
        self.tf_ts = tf_df["ts"].to_numpy(dtype="int64")
        self.tf_open = tf_df["open"].to_numpy(dtype="float64")
        self.tf_high = tf_df["high"].to_numpy(dtype="float64")
        self.tf_low = tf_df["low"].to_numpy(dtype="float64")
        self.tf_close = tf_df["close"].to_numpy(dtype="float64")
        # Pre-compute signal arrays at the leg's TF.
        sig_fn = _SIG_FUNCS[self.family]
        self.long_sig, self.short_sig = sig_fn(tf_df, self.config)
        # Position state.
        self.side: int = 0
        self.entry_price: float | None = None
        self.entry_tf_idx: int | None = None
        self.tp_pct = float(self.config["tp_pct"])
        self.sl_pct = float(self.config["sl_pct"])
        # Convert max_hold_h -> bars at the leg's TF.
        self.max_hold_bars = int(round(float(self.config["max_hold_h"]) * 60 / self.interval_min))


# ---------------------------------------------------------------------------
# Strategy (single class)
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = {
    "symbol": "BTCUSDT",
    # Sizing knob: full notional by default. Set to a fraction (0..1) to use a
    # custom entry_pct on every position open. None => use the runner default.
    "entry_pct": None,
    # If True, only step the strategy on bars marked is_new_bar (matches the
    # vectorised lab semantics). If False, every tick can drive exits/entries.
    "new_bar_only": True,
}

STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {"name": "symbol", "type": "string", "label": "Symbol (BTCUSDT only)"},
    {"name": "entry_pct", "type": "float", "min": 0.01, "max": 1.0, "step": 0.01,
     "label": "Per-trade entry % (None = full notional)"},
    {"name": "new_bar_only", "type": "bool", "label": "Step on bar close only"},
]


class MultiFactorPortfolioStrategy(Strategy):
    """17-leg equal-weight multi-factor portfolio for BTCUSDT-PERP.

    Backtest-only strategy: requires the 5 parquet files at
    ``data/perp_meta/`` (15m klines + OI + funding + taker + LSR).
    See module docstring for details and live-mode caveats.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.symbol = str(p["symbol"]).upper()
        self.entry_pct = p["entry_pct"]
        self.new_bar_only = bool(p["new_bar_only"])
        self.params = dict(p)

        self._legs: list[_LegState] = []
        self._committed_side: int = 0  # the side currently held by ctx
        self._mode: str | None = None
        self._last_bar_ts: int = 0

    # ---- lifecycle ---------------------------------------------------------
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

        if mode != "backtest":
            raise NotImplementedError(
                "MultiFactorPortfolioStrategy currently runs in BACKTEST mode only. "
                "Live trading requires funding/taker/LSR providers in src/indicators/. "
                "See docs/multi-factor-portfolio-alpha.md for the live-wiring TODO."
            )

        if self.symbol != "BTCUSDT":
            raise ValueError(
                f"This portfolio is BTCUSDT-only (was discovered on BTC perp data). "
                f"got: {self.symbol}"
            )

        unified = _load_unified_dataset(self.symbol)
        if len(unified) == 0:
            raise RuntimeError(
                f"MultiFactorPortfolioStrategy: unified dataset for {self.symbol} is empty. "
                f"Check that the configured parquet sources contain rows for the backtest window."
            )
        self._legs = [_LegState(leg, unified) for leg in ALL_LEGS]
        self._committed_side = 0
        self._last_bar_ts = 0
        self._emit_event(ctx, "MFP_INIT", {
            "n_legs": len(self._legs),
            "intervals": sorted({leg.interval_min for leg in self._legs}),
            "families": sorted({leg.family for leg in self._legs}),
            "data_rows_15m": int(len(unified)),
        })

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if self.new_bar_only and not bool(bar.get("is_new_bar", True)):
            return
        ts = _bar_ts(bar)
        if ts <= 0 or ts == self._last_bar_ts:
            return
        self._last_bar_ts = ts

        # Process exits then entries for each leg at this bar.
        for leg in self._legs:
            tf_idx = self._tf_idx_for(leg, ts)
            if tf_idx < 0:
                continue
            self._process_leg(leg, tf_idx)

        # Compute net target side from active legs.
        long_count = sum(1 for leg in self._legs if leg.side > 0)
        short_count = sum(1 for leg in self._legs if leg.side < 0)
        target = 1 if long_count > short_count else (-1 if short_count > long_count else 0)

        # Reconcile ctx position with target.
        self._reconcile(ctx, target, long_count, short_count, ts)

    # ---- internals ---------------------------------------------------------
    def _tf_idx_for(self, leg: _LegState, ts_15m: int) -> int:
        """Index of the leg's TF bar that has CLOSED at-or-before this 15m bar.

        For a 60m leg only act on the 15m bar that completes the hour: the
        15m bar's ts equals the LAST 15m boundary of the 60m candle (ts_15m
        such that (ts_15m + 15m) is a multiple of 60m).
        """
        step_ms = leg.interval_min * 60_000
        # The 15m bar closes at ts + 15m. We want the leg-TF candle that closes
        # at the same wall-clock time as (or earlier than) this 15m close.
        close_ms = ts_15m + 15 * 60_000
        # The most-recently-closed leg-TF bar has open_ts = floor((close_ms - step_ms)/step_ms)*step_ms
        candidate_open = ((close_ms - step_ms) // step_ms) * step_ms
        # We must hit it exactly (i.e. the 15m bar's close aligns with this TF's close)
        # to match the alpha-lab semantics where signals are evaluated on TF closes.
        if candidate_open + step_ms != close_ms:
            return -1
        idx = int(np.searchsorted(leg.tf_ts, candidate_open, side="left"))
        if idx >= len(leg.tf_ts) or leg.tf_ts[idx] != candidate_open:
            return -1
        return idx

    def _process_leg(self, leg: _LegState, tf_idx: int) -> None:
        # 1) Exit logic against the bar that JUST closed (idx = tf_idx).
        if leg.side != 0 and leg.entry_price is not None and leg.entry_tf_idx is not None:
            o = float(leg.tf_open[tf_idx])
            h = float(leg.tf_high[tf_idx])
            lo = float(leg.tf_low[tf_idx])
            ep = leg.entry_price
            if leg.side > 0:
                tp_level = ep * (1.0 + leg.tp_pct)
                sl_level = ep * (1.0 - leg.sl_pct)
                if o <= sl_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None
                    return
                if lo <= sl_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None
                    return
                if h >= tp_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None
                    return
            else:  # short
                tp_level = ep * (1.0 - leg.tp_pct)
                sl_level = ep * (1.0 + leg.sl_pct)
                if o >= sl_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None
                    return
                if h >= sl_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None
                    return
                if lo <= tp_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None
                    return
            # Time exit
            if (tf_idx - leg.entry_tf_idx) >= leg.max_hold_bars:
                leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None
                return

        # 2) Entry on this closed bar's signal (edge-triggered).
        if leg.side == 0 and tf_idx < len(leg.long_sig):
            if bool(leg.long_sig[tf_idx]):
                leg.side = 1
                leg.entry_price = float(leg.tf_close[tf_idx])
                leg.entry_tf_idx = tf_idx
            elif bool(leg.short_sig[tf_idx]):
                leg.side = -1
                leg.entry_price = float(leg.tf_close[tf_idx])
                leg.entry_tf_idx = tf_idx

    def _reconcile(self, ctx: StrategyContext, target: int, long_count: int,
                    short_count: int, ts: int) -> None:
        cur = self._committed_side
        if target == cur:
            return
        # Flatten if currently in opposite-or-neutral target.
        if cur != 0 and target != cur:
            try:
                ctx.close_position(reason="MFP: net direction flip/flat")
            except Exception:  # noqa: BLE001
                pass
            self._committed_side = 0
            self._emit_event(ctx, "MFP_FLAT", {
                "ts": ts, "long_legs": long_count, "short_legs": short_count,
            })
        # Open new position in target direction.
        if target == 1:
            kw: dict[str, Any] = {"reason": f"MFP: net long ({long_count}>{short_count})"}
            if self.entry_pct is not None:
                kw["entry_pct"] = float(self.entry_pct)
            ctx.enter_long(**kw)
            self._committed_side = 1
            self._emit_event(ctx, "MFP_ENTER_LONG", {
                "ts": ts, "long_legs": long_count, "short_legs": short_count,
            })
        elif target == -1:
            kw = {"reason": f"MFP: net short ({short_count}>{long_count})"}
            if self.entry_pct is not None:
                kw["entry_pct"] = float(self.entry_pct)
            ctx.enter_short(**kw)
            self._committed_side = -1
            self._emit_event(ctx, "MFP_ENTER_SHORT", {
                "ts": ts, "long_legs": long_count, "short_legs": short_count,
            })

    @staticmethod
    def _emit_event(ctx: Any, action: str, data: dict[str, Any]) -> None:
        fn = getattr(ctx, "log_event", None)
        if not callable(fn):
            return
        try:
            fn(action, data)
        except Exception:  # noqa: BLE001
            pass


def _bar_ts(bar: dict[str, Any]) -> int:
    for key in ("bar_timestamp", "timestamp"):
        try:
            v = int(bar.get(key, 0) or 0)
        except Exception:  # noqa: BLE001
            v = 0
        if v > 0:
            return v
    return 0
