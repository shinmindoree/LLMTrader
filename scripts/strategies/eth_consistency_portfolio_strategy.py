"""30-leg ETHUSDT-PERP consistency portfolio strategy.

Discovered by the alpha-lab ETH consistency optimizer (eth_lib -> eth_optimize
-> eth_finalize). The leg universe was generated as a diverse grid across 7
signal families, every leg backtested over the FULL ETH range
(2021-12-01 .. 2026-06-03), then a full-window maximin LP (per-leg weight
optimizer, 6% diversification cap) selected 30 legs that maximize the worst
calendar month.

==============================================================================
WHAT THIS STRATEGY IS (and the honest performance picture)
==============================================================================
This file reuses the proven Multi-Factor-Portfolio (MFP) runtime engine and
runs the 30 discovered legs in a SINGLE job using equal-vote net direction
(long if more legs are long than short, else short, else flat). Sizing is
delegated to the runner trade-settings (``max_position`` / ``max_order``).

Three performance views (full ETH range, see eth_consistency_report.json):

  1. LP-weighted independent-sleeve (IN-SAMPLE, best case):
       55/55 months positive, MDD 2.9%, +15.1%.
     This is the literal "every month positive" target. It is reproduced by
     deploying each leg as its own 1/n notional job sized by the
     ``lp_weight_reference`` weights (data/strategy_params/.../ETHUSDT.json),
     NOT by this single equal-vote job. It uses the whole history to fit the
     weights, so it is in-sample / best-case.

  2. Deployed equal-vote single job (IN-SAMPLE, full window):
       39/55 months positive, MDD 3.35%, +26.0%.

  3. Walk-forward (LIVE-REALISTIC, expanding-window refit, 12-month warmup):
       equal:   72.1% positive months, MDD 1.38%, +17.9%
       minvar:  55.8% positive months, MDD 0.84%, +11.8%
       maximin: 60.5% positive months, MDD 3.78%, +7.6%
     Genuine every-month-positive is NOT achievable out-of-sample on ETH; the
     realistic ceiling is ~70% positive months. Equal weighting is the most
     robust live choice and is what this single-job strategy uses.

Aggregate trade frequency (sum of the 30 legs' per-day trade counts): ~6.1
trades/day, comfortably above the >= 2/day requirement.

==============================================================================
RUNTIME CONTRACT
==============================================================================
Identical to MultiFactorPortfolioStrategy (this class subclasses it):
  - Base candle interval 15m; resampled internally to 30m/60m/120m/240m.
  - Requires 5 parquet files at data/perp_meta/ (or the MFP_PARQUET_* env
    resolvers): ETHUSDT_15m_klines / _oi_5m / _funding / _taker_5m / _lsr_5m.
  - Net position semantics: equal-vote majority direction across active legs;
    per-leg SL/TP/TIME exits update leg state. Conservative SL fill semantics
    match the vectorised lab.
  - LIVE mode uses the same Redis-backed providers as MFP (OI / funding /
    taker / LSR ingestors) plus Binance REST for kline gap-fill.

This subclass differs from MFP in only three ways:
  1. A different, larger leg set (30 ETH-optimized legs vs MFP's 17).
  2. Two extra signal families registered into the engine: ``session_mr``
     and ``donchian_pullback``.
  3. ``ETHUSDT`` runs the embedded legs directly (no param-store promotion
     gate), since the leg STRUCTURE itself is the discovered artifact.
==============================================================================
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import talib

# The runner loads strategy files standalone (exec_module) without adding the
# strategies directory to sys.path, so add it here to import the shared MFP
# engine. Also add <repo>/src for the Strategy base (MFP does this too).
_THIS_DIR = Path(__file__).resolve().parent
_SRC = Path(__file__).resolve().parents[2] / "src"
for _p in (str(_THIS_DIR), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import multi_factor_portfolio_strategy as mfp  # noqa: E402


# ---------------------------------------------------------------------------
# Extra signal families not present in the MFP engine (ported verbatim from
# scripts/_alpha_lab/strategies.py, adapted to the (df, config) calling
# convention used by mfp._SIG_FUNCS).
# ---------------------------------------------------------------------------
def _sig_session_mr(df: pd.DataFrame, c: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Time-of-day filtered mean reversion (BTC/ETH range during a session)."""
    cl = df["close"].to_numpy(dtype="float64")
    rsi = talib.RSI(cl, timeperiod=int(c.get("rsi_period", 7)))
    long_sig = rsi <= float(c["rsi_long"])
    short_sig = rsi >= float(c["rsi_short"])
    if c.get("require_bb_touch", True):
        upper, _mid, lower = talib.BBANDS(
            cl, timeperiod=int(c["bb_period"]),
            nbdevup=float(c["bb_std"]), nbdevdn=float(c["bb_std"]),
        )
        long_sig = long_sig & (cl <= lower)
        short_sig = short_sig & (cl >= upper)

    dts = pd.to_datetime(df["ts"].to_numpy(dtype="int64"), unit="ms", utc=True)
    hours = dts.hour.to_numpy(dtype="int64")
    hs, he = int(c["hour_start"]), int(c["hour_end"])
    if hs <= he:
        hour_ok = (hours >= hs) & (hours < he)
    else:
        hour_ok = (hours >= hs) | (hours < he)
    long_sig = long_sig & hour_ok
    short_sig = short_sig & hour_ok

    if c.get("use_atr_filter", True):
        atrp = mfp._atr_pct(df, period=int(c.get("atr_period", 14)))
        ok = (atrp >= float(c["atr_min_pct"])) & (atrp <= float(c["atr_max_pct"]))
        long_sig = long_sig & ok
        short_sig = short_sig & ok
    return mfp._apply_side(long_sig, short_sig, c.get("side", "both"))


def _sig_donchian_pullback(df: pd.DataFrame, c: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Donchian-channel pullback: enter on retracement inside the channel."""
    h = df["high"].to_numpy(dtype="float64")
    lo = df["low"].to_numpy(dtype="float64")
    cl = df["close"].to_numpy(dtype="float64")
    dc = int(c["dc_period"])
    upper = pd.Series(h).shift(1).rolling(dc, min_periods=dc).max().to_numpy("float64")
    lower = pd.Series(lo).shift(1).rolling(dc, min_periods=dc).min().to_numpy("float64")
    rng = upper - lower
    pb = float(c.get("dc_pullback", 0.382))
    long_sig = (cl <= lower + pb * rng) & (cl > lower)
    short_sig = (cl >= upper - pb * rng) & (cl < upper)

    rsi = talib.RSI(cl, timeperiod=int(c.get("rsi_period", 14)))
    long_sig = long_sig & (rsi <= float(c["rsi_long"]))
    short_sig = short_sig & (rsi >= float(c["rsi_short"]))

    if c.get("use_oi", False):
        oi_pct = mfp._pct_change_n(df["oi"].to_numpy(dtype="float64"), int(c["oi_lb"]))
        long_sig = long_sig & (oi_pct >= float(c["oi_min_for_long"]))
        short_sig = short_sig & (oi_pct <= float(c["oi_max_for_short"]))
    return mfp._apply_side(long_sig, short_sig, c.get("side", "both"))


# Register the extra families into the shared engine's dispatch table so
# mfp._LegState can build signals for every leg below. Additive only.
mfp._SIG_FUNCS.setdefault("session_mr", _sig_session_mr)
mfp._SIG_FUNCS.setdefault("donchian_pullback", _sig_donchian_pullback)


# ---------------------------------------------------------------------------
# The 30 discovered ETH legs (frozen from
# data/strategy_params/eth_consistency_portfolio/ETHUSDT.json).
# Deployed equal-weight (each leg 1/30 notional, equal-vote net direction).
# ---------------------------------------------------------------------------
ALL_LEGS: list[dict[str, Any]] = [
    {"family": 'lsr_taker_confluence',
     "config": {'interval_min': 15, 'lsr_lb': 240, 'z_lsr_long': -1.0, 'z_lsr_short': 1.5, 'use_taker': True, 'taker_lb': 240, 'z_taker_long': -1.0, 'z_taker_short': 1.0, 'lsr_col': 'lsr_count', 'use_rsi': True, 'rsi_long': 35.0, 'rsi_short': 65.0, 'tp_pct': 0.012, 'sl_pct': 0.012, 'max_hold_h': 16, 'side': 'short_only'}},
    {"family": 'lsr_taker_confluence',
     "config": {'interval_min': 15, 'lsr_lb': 240, 'z_lsr_long': -1.0, 'z_lsr_short': 1.5, 'use_taker': True, 'taker_lb': 240, 'z_taker_long': -1.0, 'z_taker_short': 1.0, 'lsr_col': 'lsr_count', 'use_rsi': True, 'rsi_long': 35.0, 'rsi_short': 65.0, 'tp_pct': 0.018, 'sl_pct': 0.012, 'max_hold_h': 8, 'side': 'short_only'}},
    {"family": 'oi_z_combo',
     "config": {'interval_min': 60, 'oi_lb': 96, 'z_lookback': 480, 'z_long': -1.5, 'z_short': 2.0, 'use_rsi': True, 'rsi_long_max': 45.0, 'rsi_short_min': 65.0, 'use_taker': False, 'taker_long_max': 0.95, 'taker_short_min': 1.05, 'use_atr_filter': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'tp_pct': 0.025, 'sl_pct': 0.012, 'max_hold_h': 16, 'side': 'short_only'}},
    {"family": 'oi_z_combo',
     "config": {'interval_min': 60, 'oi_lb': 96, 'z_lookback': 480, 'z_long': -2.0, 'z_short': 2.0, 'use_rsi': True, 'rsi_long_max': 45.0, 'rsi_short_min': 65.0, 'use_taker': False, 'taker_long_max': 0.95, 'taker_short_min': 1.05, 'use_atr_filter': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'tp_pct': 0.025, 'sl_pct': 0.012, 'max_hold_h': 16, 'side': 'short_only'}},
    {"family": 'oi_z_combo',
     "config": {'interval_min': 60, 'oi_lb': 96, 'z_lookback': 480, 'z_long': -2.0, 'z_short': 2.0, 'use_rsi': True, 'rsi_long_max': 45.0, 'rsi_short_min': 65.0, 'use_taker': False, 'taker_long_max': 0.95, 'taker_short_min': 1.05, 'use_atr_filter': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'tp_pct': 0.018, 'sl_pct': 0.012, 'max_hold_h': 16, 'side': 'short_only'}},
    {"family": 'pullback_in_trend',
     "config": {'interval_min': 120, 'htf_ema_period': 200, 'pullback_rsi_period': 7, 'rsi_long': 20.0, 'rsi_short': 80.0, 'use_bb': True, 'bb_period': 20, 'bb_std': 2.0, 'use_atr_floor': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'use_funding': False, 'tp_pct': 0.03, 'sl_pct': 0.015, 'max_hold_h': 24, 'side': 'long_only'}},
    {"family": 'pullback_in_trend',
     "config": {'interval_min': 120, 'htf_ema_period': 100, 'pullback_rsi_period': 7, 'rsi_long': 30.0, 'rsi_short': 70.0, 'use_bb': True, 'bb_period': 20, 'bb_std': 2.0, 'use_atr_floor': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'use_funding': False, 'tp_pct': 0.015, 'sl_pct': 0.01, 'max_hold_h': 8, 'side': 'both'}},
    {"family": 'pullback_in_trend',
     "config": {'interval_min': 60, 'htf_ema_period': 100, 'pullback_rsi_period': 7, 'rsi_long': 30.0, 'rsi_short': 70.0, 'use_bb': True, 'bb_period': 20, 'bb_std': 2.0, 'use_atr_floor': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'use_funding': False, 'tp_pct': 0.03, 'sl_pct': 0.015, 'max_hold_h': 24, 'side': 'both'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 120, 'dc_period': 96, 'atr_min_mult': 0.0, 'use_oi': True, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.06, 'sl_pct': 0.02, 'max_hold_h': 24, 'side': 'long_only'}},
    {"family": 'donchian_pullback',
     "config": {'interval_min': 60, 'dc_period': 192, 'dc_pullback': 0.382, 'rsi_long': 40.0, 'rsi_short': 60.0, 'use_oi': False, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': 0.0, 'tp_pct': 0.04, 'sl_pct': 0.02, 'max_hold_h': 48, 'side': 'short_only'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 240, 'dc_period': 48, 'atr_min_mult': 0.0, 'use_oi': False, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.06, 'sl_pct': 0.02, 'max_hold_h': 24, 'side': 'long_only'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 240, 'dc_period': 192, 'atr_min_mult': 0.0, 'use_oi': False, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.06, 'sl_pct': 0.02, 'max_hold_h': 24, 'side': 'short_only'}},
    {"family": 'oi_z_combo',
     "config": {'interval_min': 60, 'oi_lb': 96, 'z_lookback': 480, 'z_long': -1.5, 'z_short': 2.0, 'use_rsi': True, 'rsi_long_max': 45.0, 'rsi_short_min': 65.0, 'use_taker': False, 'taker_long_max': 0.95, 'taker_short_min': 1.05, 'use_atr_filter': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'tp_pct': 0.018, 'sl_pct': 0.012, 'max_hold_h': 16, 'side': 'short_only'}},
    {"family": 'donchian_pullback',
     "config": {'interval_min': 240, 'dc_period': 96, 'dc_pullback': 0.382, 'rsi_long': 40.0, 'rsi_short': 60.0, 'use_oi': False, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': 0.0, 'tp_pct': 0.03, 'sl_pct': 0.012, 'max_hold_h': 16, 'side': 'long_only'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 240, 'dc_period': 192, 'atr_min_mult': 0.0, 'use_oi': True, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.08, 'sl_pct': 0.025, 'max_hold_h': 48, 'side': 'long_only'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 60, 'dc_period': 48, 'atr_min_mult': 0.0, 'use_oi': True, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.12, 'sl_pct': 0.03, 'max_hold_h': 96, 'side': 'long_only'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 240, 'dc_period': 192, 'atr_min_mult': 0.0, 'use_oi': False, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.05, 'sl_pct': 0.02, 'max_hold_h': 48, 'side': 'short_only'}},
    {"family": 'pullback_in_trend',
     "config": {'interval_min': 30, 'htf_ema_period': 200, 'pullback_rsi_period': 7, 'rsi_long': 25.0, 'rsi_short': 75.0, 'use_bb': True, 'bb_period': 20, 'bb_std': 2.0, 'use_atr_floor': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'use_funding': False, 'tp_pct': 0.03, 'sl_pct': 0.015, 'max_hold_h': 24, 'side': 'long_only'}},
    {"family": 'donchian_pullback',
     "config": {'interval_min': 120, 'dc_period': 96, 'dc_pullback': 0.382, 'rsi_long': 40.0, 'rsi_short': 60.0, 'use_oi': False, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': 0.0, 'tp_pct': 0.03, 'sl_pct': 0.012, 'max_hold_h': 16, 'side': 'long_only'}},
    {"family": 'pullback_in_trend',
     "config": {'interval_min': 120, 'htf_ema_period': 100, 'pullback_rsi_period': 7, 'rsi_long': 25.0, 'rsi_short': 75.0, 'use_bb': True, 'bb_period': 20, 'bb_std': 2.0, 'use_atr_floor': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'use_funding': False, 'tp_pct': 0.03, 'sl_pct': 0.015, 'max_hold_h': 24, 'side': 'both'}},
    {"family": 'pullback_in_trend',
     "config": {'interval_min': 60, 'htf_ema_period': 100, 'pullback_rsi_period': 7, 'rsi_long': 30.0, 'rsi_short': 70.0, 'use_bb': True, 'bb_period': 20, 'bb_std': 2.0, 'use_atr_floor': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'use_funding': False, 'tp_pct': 0.02, 'sl_pct': 0.012, 'max_hold_h': 16, 'side': 'long_only'}},
    {"family": 'oi_z_combo',
     "config": {'interval_min': 60, 'oi_lb': 192, 'z_lookback': 480, 'z_long': -1.5, 'z_short': 1.5, 'use_rsi': True, 'rsi_long_max': 45.0, 'rsi_short_min': 65.0, 'use_taker': False, 'taker_long_max': 0.95, 'taker_short_min': 1.05, 'use_atr_filter': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'tp_pct': 0.025, 'sl_pct': 0.012, 'max_hold_h': 16, 'side': 'short_only'}},
    {"family": 'ensemble_meanrev',
     "config": {'interval_min': 15, 'bb_period': 20, 'bb_std': 2.0, 'rsi_period': 14, 'rsi_long': 35.0, 'rsi_short': 65.0, 'oi_lb': 96, 'oi_drop': -0.015, 'oi_pop': 0.02, 'taker_lb': 96, 'taker_long_max': 0.93, 'taker_short_min': 1.07, 'lsr_z_lookback': 480, 'lsr_z_long': -1.0, 'lsr_z_short': 1.5, 'min_votes': 2, 'use_trend_filter': True, 'trend_ema': 200, 'use_atr_filter': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'tp_pct': 0.02, 'sl_pct': 0.01, 'max_hold_h': 16, 'side': 'short_only'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 60, 'dc_period': 24, 'atr_min_mult': 0.0, 'use_oi': False, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.08, 'sl_pct': 0.03, 'max_hold_h': 96, 'side': 'short_only'}},
    {"family": 'ensemble_meanrev',
     "config": {'interval_min': 15, 'bb_period': 20, 'bb_std': 2.0, 'rsi_period': 14, 'rsi_long': 40.0, 'rsi_short': 60.0, 'oi_lb': 96, 'oi_drop': -0.015, 'oi_pop': 0.02, 'taker_lb': 96, 'taker_long_max': 0.93, 'taker_short_min': 1.07, 'lsr_z_lookback': 480, 'lsr_z_long': -1.0, 'lsr_z_short': 1.5, 'min_votes': 2, 'use_trend_filter': True, 'trend_ema': 200, 'use_atr_filter': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'tp_pct': 0.025, 'sl_pct': 0.012, 'max_hold_h': 24, 'side': 'both'}},
    {"family": 'session_mr',
     "config": {'interval_min': 15, 'rsi_period': 7, 'rsi_long': 20.0, 'rsi_short': 80.0, 'bb_period': 20, 'bb_std': 2.5, 'require_bb_touch': True, 'hour_start': 7, 'hour_end': 15, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'tp_pct': 0.02, 'sl_pct': 0.012, 'max_hold_h': 12, 'side': 'short_only'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 240, 'dc_period': 48, 'atr_min_mult': 0.0, 'use_oi': False, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.12, 'sl_pct': 0.03, 'max_hold_h': 96, 'side': 'short_only'}},
    {"family": 'ensemble_meanrev',
     "config": {'interval_min': 15, 'bb_period': 20, 'bb_std': 2.0, 'rsi_period': 14, 'rsi_long': 30.0, 'rsi_short': 70.0, 'oi_lb': 96, 'oi_drop': -0.015, 'oi_pop': 0.02, 'taker_lb': 96, 'taker_long_max': 0.93, 'taker_short_min': 1.07, 'lsr_z_lookback': 480, 'lsr_z_long': -1.0, 'lsr_z_short': 1.5, 'min_votes': 2, 'use_trend_filter': True, 'trend_ema': 200, 'use_atr_filter': True, 'atr_min_pct': 0.0025, 'atr_max_pct': 0.025, 'tp_pct': 0.02, 'sl_pct': 0.01, 'max_hold_h': 16, 'side': 'short_only'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 240, 'dc_period': 96, 'atr_min_mult': 0.0, 'use_oi': True, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.08, 'sl_pct': 0.025, 'max_hold_h': 48, 'side': 'long_only'}},
    {"family": 'donchian_breakout',
     "config": {'interval_min': 240, 'dc_period': 192, 'atr_min_mult': 0.0, 'use_oi': True, 'oi_lb': 96, 'oi_min_for_long': 0.0, 'oi_max_for_short': -0.005, 'require_close_above': True, 'tp_pct': 0.08, 'sl_pct': 0.03, 'max_hold_h': 96, 'side': 'long_only'}},
]

assert len(ALL_LEGS) == 30, f"expected 30 legs, got {len(ALL_LEGS)}"

BASELINE_SYMBOL = "ETHUSDT"
STRATEGY_ID = "eth_consistency_portfolio"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class EthConsistencyPortfolioStrategy(mfp.MultiFactorPortfolioStrategy):
    """30-leg ETHUSDT-PERP consistency portfolio (equal-vote single job).

    Subclasses the MFP engine and swaps in the 30 ETH-optimized legs. The
    leg STRUCTURE is the discovered artifact, so ``ETHUSDT`` runs the
    embedded legs directly without a param-store promotion gate. All other
    runtime behaviour (15m base feed, internal resampling, conservative SL
    fills, equal-vote net direction, live Redis providers, state
    persistence) is inherited unchanged from
    ``MultiFactorPortfolioStrategy``.
    """

    def initialize(self, ctx: Any) -> None:
        # Reuse the parent's full backtest/live initialize (data load,
        # gap-fill, leg build, events) but source the leg list + symbol
        # gate from THIS strategy's embedded universe. The parent's
        # initialize references the module-level ``resolve_legs`` and
        # ``_symbol_supported`` as globals, so swap them for the duration
        # of the call.
        orig_resolve = mfp.resolve_legs
        orig_supported = mfp._symbol_supported
        mfp.resolve_legs = lambda symbol: [
            {"family": leg["family"], "config": dict(leg["config"])}
            for leg in ALL_LEGS
        ]
        mfp._symbol_supported = lambda symbol: True
        try:
            super().initialize(ctx)
        finally:
            mfp.resolve_legs = orig_resolve
            mfp._symbol_supported = orig_supported
