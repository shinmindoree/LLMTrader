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
  net direction. Sizing is delegated to the runner trade-settings (i.e.
  `max_position` / `max_order`); the strategy calls
  `enter_long`/`enter_short` without an explicit entry_pct. For true
  1/17-each notional weighting, deploy 17 separate runner jobs and set
  trade-settings `max_position ≈ 5.88%` (= 100/17) on each one.
- LIVE mode: requires the perp-meta ingestor (``scripts/perp_meta_ingestor.py``,
  see ``infra/docs/perp-meta-ingestor-deployment.md``) running and writing
  to Redis ZSETs ``funding:{S}:hist`` / ``taker:{S}:hist`` / ``lsr:{S}:hist``,
  plus the OI ingestor (``oi:{S}:hist``). At ``initialize()`` the strategy
  loads the parquet seed, gap-fills the window from Redis (and Binance for
  klines), then keeps the unified dataset rolling on every new 15m bar.
  Required env when running live:
    REDIS_URL  (or REDIS_HOST + REDIS_USERNAME for AAD)
    AZURE_BLOB_ACCOUNT_URL + MFP_PARQUET_BLOB_CONTAINER + MFP_PARQUET_BLOB_PREFIX
  Optional:
    MFP_LIVE_HISTORY_BARS_15M (default 5760 = 60 days of 15m bars)

==============================================================================
"""
from __future__ import annotations

import asyncio
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
# Per-symbol parameter resolution
# ---------------------------------------------------------------------------
# Symbol whose thresholds are baked into ALL_LEGS above (discovery origin).
BASELINE_SYMBOL = "BTCUSDT"

# Stable identifier used to key parameter artifacts in the param store.
STRATEGY_ID = "multi_factor_portfolio"

# Threshold fields that may be re-fitted per symbol. Structural fields
# (interval_min, lookbacks, periods, use_* flags, side, ...) are deliberately
# excluded: the leg STRUCTURE is fixed to the BTC-validated baseline and only
# these volatility-sensitive thresholds are overridden per symbol.
TUNABLE_FIELDS: frozenset[str] = frozenset({
    "tp_pct", "sl_pct", "max_hold_h",
    "z_long", "z_short",
    "z_lsr_long", "z_lsr_short", "z_taker_long", "z_taker_short",
    "lsr_z_long", "lsr_z_short",
    "rsi_long", "rsi_short", "rsi_long_max", "rsi_short_min",
    "atr_min_pct", "atr_max_pct", "atr_min_mult",
    "taker_long_max", "taker_short_min",
    "oi_drop", "oi_pop",
    "bb_std",
    "fund_long", "fund_short",
    "oi_max_for_long", "oi_min_for_short",
})


def _apply_leg_overrides(
    baseline: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a copy of ``baseline`` with per-leg threshold overrides applied.

    Only fields in ``TUNABLE_FIELDS`` are overridden; structural keys in an
    override are ignored (with a warning) so a re-fit can never alter the leg
    structure. ``overrides`` must have one dict per baseline leg.
    """
    if len(overrides) != len(baseline):
        raise ValueError(
            f"leg_overrides length {len(overrides)} != baseline legs "
            f"{len(baseline)}"
        )
    out: list[dict[str, Any]] = []
    for i, (leg, ov) in enumerate(zip(baseline, overrides)):
        cfg = dict(leg["config"])
        for k, v in (ov or {}).items():
            if k not in TUNABLE_FIELDS:
                logger.warning(
                    "[mfp] leg %d: ignoring non-tunable override field %r "
                    "(structure is fixed to baseline)", i, k,
                )
                continue
            if k not in cfg:
                logger.warning(
                    "[mfp] leg %d: override field %r not in baseline config; "
                    "skipping", i, k,
                )
                continue
            cfg[k] = v
        out.append({"family": leg["family"], "config": cfg})
    return out


def _symbol_supported(symbol: str) -> bool:
    """True if ``symbol`` can run: baseline symbol, or has a promoted artifact."""
    sym = symbol.upper()
    if sym == BASELINE_SYMBOL:
        return True
    try:
        from strategy.param_store import has_promoted
    except Exception:  # noqa: BLE001
        return False
    return has_promoted(STRATEGY_ID, sym)


def resolve_legs(symbol: str) -> list[dict[str, Any]]:
    """Resolve the leg list for ``symbol``.

    - ``BASELINE_SYMBOL`` (BTCUSDT): returns ``ALL_LEGS`` unchanged so the
      discovered BTC portfolio is reproduced byte-for-byte.
    - Other symbols: requires a promoted param artifact and applies its
      ``leg_overrides`` to the fixed baseline structure.
    """
    sym = symbol.upper()
    if sym == BASELINE_SYMBOL:
        return ALL_LEGS
    from strategy.param_store import load_promoted

    art = load_promoted(STRATEGY_ID, sym)
    if art is None or not art.leg_overrides:
        raise ValueError(
            f"MultiFactorPortfolioStrategy: no promoted parameter artifact for "
            f"{sym}. Run scripts/discover_mfp_params.py --symbol {sym} to "
            f"sweep+OOS-validate thresholds, then promote the result."
        )
    return _apply_leg_overrides(ALL_LEGS, art.leg_overrides)


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


def _fetch_klines_15m_sync(symbol: str, start_ms: int, end_ms: int) -> list[list]:
    """Fetch 15m klines via Binance USDM REST. Used for live-mode gap-fill.

    Returns a list of `[open_time, open, high, low, close, volume, ...]` raw
    rows ordered by open_time. Paginates 1000 rows at a time. Times are
    inclusive at both ends.
    """
    import httpx  # local import: only needed in live mode
    if start_ms > end_ms:
        return []
    base = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com")
    url = f"{base}/fapi/v1/klines"
    bar_ms = 15 * 60 * 1000
    chunk_ms = 1000 * bar_ms
    out: list[list] = []
    cur = start_ms
    with httpx.Client(timeout=20.0) as cli:
        while cur <= end_ms:
            params = {
                "symbol": symbol.upper(),
                "interval": "15m",
                "startTime": int(cur),
                "endTime": int(min(cur + chunk_ms, end_ms)),
                "limit": 1000,
            }
            for attempt in range(5):
                try:
                    resp = cli.get(url, params=params)
                    if resp.status_code == 429 or resp.status_code >= 500:
                        import time as _t
                        _t.sleep(2 ** attempt)
                        continue
                    resp.raise_for_status()
                    rows = resp.json() or []
                    out.extend(rows)
                    if not rows:
                        cur = cur + chunk_ms + bar_ms
                    else:
                        last_open = int(rows[-1][0])
                        cur = last_open + bar_ms
                    break
                except httpx.HTTPError as exc:
                    if attempt == 4:
                        raise
                    import time as _t
                    _t.sleep(2 ** attempt)
            else:
                break
    # Dedupe by open_time and sort.
    by_ts: dict[int, list] = {}
    for r in out:
        try:
            ts = int(r[0])
        except Exception:  # noqa: BLE001
            continue
        if start_ms <= ts <= end_ms:
            by_ts[ts] = r
    return [by_ts[k] for k in sorted(by_ts)]


# ---------------------------------------------------------------------------
# Auxiliary series REST fetchers (backtest gap-fill).
#
# Live mode reads OI/funding/taker/LSR through Redis providers populated by
# the ingestors. Backtests historically had only the parquet seed, so once the
# backtest window extended past the seed's last timestamp the unified DF froze
# at the seed boundary and no signals fired. To restore live/backtest parity
# we fetch the same aux series from Binance REST for the gap window and serve
# them through ``_RestSeriesProvider`` (last-known-at-or-before semantics).
# ---------------------------------------------------------------------------

# Binance ``/futures/data/*`` endpoints reject ``startTime`` older than ~30d.
# Use a safety margin so requests near the boundary do not trip ``-1130``.
_FUTURES_DATA_LOOKBACK_MS = 30 * 24 * 3600 * 1000 - 30 * 60 * 1000

_SERIES_CONFIG: dict[str, dict[str, Any]] = {
    "oi": {
        "path": "/futures/data/openInterestHist",
        "ts_field": "timestamp",
        "value_field": "sumOpenInterest",
        "period_ms": 5 * 60 * 1000,
        "limit": 500,
        "extra_params": {"period": "5m"},
        "lookback_cap_ms": _FUTURES_DATA_LOOKBACK_MS,
    },
    "funding": {
        "path": "/fapi/v1/fundingRate",
        "ts_field": "fundingTime",
        "value_field": "fundingRate",
        # Funding posts every 8h; lower bound used only for cursor padding.
        "period_ms": 8 * 60 * 60 * 1000,
        "limit": 1000,
        "extra_params": {},
        "lookback_cap_ms": None,
    },
    "taker": {
        "path": "/futures/data/takerlongshortRatio",
        "ts_field": "timestamp",
        "value_field": "buySellRatio",
        "period_ms": 5 * 60 * 1000,
        "limit": 500,
        "extra_params": {"period": "5m"},
        "lookback_cap_ms": _FUTURES_DATA_LOOKBACK_MS,
    },
    "lsr": {
        "path": "/futures/data/globalLongShortAccountRatio",
        "ts_field": "timestamp",
        "value_field": "longShortRatio",
        "period_ms": 5 * 60 * 1000,
        "limit": 500,
        "extra_params": {"period": "5m"},
        "lookback_cap_ms": _FUTURES_DATA_LOOKBACK_MS,
    },
}


def _fetch_binance_series_sync(
    kind: str, symbol: str, start_ms: int, end_ms: int
) -> list[dict]:
    """Paginated fetch for a single aux series. Returns raw row dicts.

    Mirrors the cursor logic used by ``scripts/oi_ingestor.py`` and
    ``scripts/perp_meta_ingestor.py``: each Binance request returns up to
    ``limit`` rows within ``[startTime, endTime]``, so we step forward in
    ``limit * period_ms`` chunks until the cursor passes ``end_ms``.
    """
    import httpx  # local import: only used during live/backtest gap-fill
    if start_ms > end_ms:
        return []
    cfg = _SERIES_CONFIG[kind]
    base = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com")
    url = f"{base}{cfg['path']}"
    period_ms = int(cfg["period_ms"])
    limit = int(cfg["limit"])
    chunk_ms = limit * period_ms
    lookback_cap_ms = cfg.get("lookback_cap_ms")
    cursor = start_ms
    if lookback_cap_ms is not None:
        # Endpoint rejects startTime older than now-30d; clamp so we don't
        # waste a request that would either fail (-1130) or return [].
        cursor = max(cursor, end_ms - int(lookback_cap_ms))
    out: list[dict] = []
    # Hard cap on pagination iterations as a guard against pathological cursor
    # advancement; 400 pages = 400*limit rows which is more than 30d of 5m data.
    page = 0
    with httpx.Client(timeout=20.0) as cli:
        while cursor <= end_ms and page < 400:
            page += 1
            chunk_end = min(end_ms, cursor + chunk_ms)
            params: dict[str, Any] = {
                "symbol": symbol.upper(),
                "limit": limit,
                "startTime": int(cursor),
                "endTime": int(chunk_end),
            }
            params.update(cfg["extra_params"])
            rows: list[dict] = []
            for attempt in range(5):
                try:
                    resp = cli.get(url, params=params)
                    if resp.status_code == 429 or resp.status_code >= 500:
                        import time as _t
                        _t.sleep(2 ** attempt)
                        continue
                    resp.raise_for_status()
                    payload = resp.json()
                    rows = payload if isinstance(payload, list) else []
                    break
                except httpx.HTTPError:
                    if attempt == 4:
                        raise
                    import time as _t
                    _t.sleep(2 ** attempt)
            else:
                break
            if not rows:
                cursor = chunk_end + 1
                continue
            out.extend(rows)
            try:
                last_ts = int(rows[-1][cfg["ts_field"]])
            except (KeyError, TypeError, ValueError):
                cursor = chunk_end + 1
                continue
            next_cursor = last_ts + period_ms
            cursor = next_cursor if next_cursor > cursor else chunk_end + 1
    return out


def _build_rest_series_arrays(
    kind: str, symbol: str, start_ms: int, end_ms: int
) -> tuple[np.ndarray, np.ndarray]:
    """Fetch + parse a single aux series into sorted ``(times_ms, values)``.

    The requested window is padded backwards by two periods so the resulting
    provider always has an anchor entry at-or-before ``start_ms``. This makes
    ``value_at(ts)`` return a real number for the very first bars of the
    gap-fill window (otherwise an 8h-cadence series like funding would return
    NaN for hours after the parquet boundary).
    """
    cfg = _SERIES_CONFIG[kind]
    period_ms = int(cfg["period_ms"])
    pad_ms = 2 * period_ms
    fetch_start = max(0, int(start_ms) - pad_ms)
    raw = _fetch_binance_series_sync(kind, symbol, fetch_start, end_ms)
    by_ts: dict[int, float] = {}
    for r in raw:
        try:
            ts = int(r[cfg["ts_field"]])
            v = float(r[cfg["value_field"]])
        except (KeyError, TypeError, ValueError):
            continue
        if fetch_start <= ts <= end_ms and math.isfinite(v):
            by_ts[ts] = v
    if not by_ts:
        return np.empty(0, dtype="int64"), np.empty(0, dtype="float64")
    ts_sorted = sorted(by_ts.keys())
    return (
        np.asarray(ts_sorted, dtype="int64"),
        np.asarray([by_ts[t] for t in ts_sorted], dtype="float64"),
    )


class _RestSeriesProvider:
    """Pre-fetched aux series served via ``value_at(ts)``.

    Matches the contract of the live Redis providers (``value_at`` /
    ``_value_at``) so ``_provider_value_at`` can call into either without
    branching. Lookup uses ``np.searchsorted`` to return the most recent
    sample at-or-before ``ts`` — same semantics as ``_last_known`` used when
    seeding aux columns from the parquet path.
    """

    __slots__ = ("_times", "_values", "_kind")

    def __init__(self, times: np.ndarray, values: np.ndarray, kind: str) -> None:
        self._times = np.asarray(times, dtype="int64")
        self._values = np.asarray(values, dtype="float64")
        self._kind = kind

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def size(self) -> int:
        return int(self._times.size)

    @property
    def coverage(self) -> tuple[int, int] | None:
        if self._times.size == 0:
            return None
        return (int(self._times[0]), int(self._times[-1]))

    def value_at(self, ts: int) -> float:
        if self._times.size == 0:
            return float("nan")
        idx = int(np.searchsorted(self._times, int(ts), side="right")) - 1
        if idx < 0:
            return float("nan")
        return float(self._values[idx])


def _build_backtest_rest_providers(
    symbol: str, start_ms: int, end_ms: int
) -> dict[str, _RestSeriesProvider]:
    """Fetch OI/funding/taker/LSR for the gap window and wrap each as a provider."""
    out: dict[str, _RestSeriesProvider] = {}
    for kind in ("oi", "funding", "taker", "lsr"):
        times, values = _build_rest_series_arrays(kind, symbol, start_ms, end_ms)
        out[kind] = _RestSeriesProvider(times, values, kind)
        logger.info(
            "[mfp] backtest REST aux fetch kind=%s rows=%d coverage=%s",
            kind,
            int(times.size),
            (int(times[0]), int(times[-1])) if times.size else None,
        )
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
        "long_sig", "short_sig", "side", "entry_price", "entry_tf_idx", "entry_tf_ts",
        "tp_pct", "sl_pct", "max_hold_bars", "entry_clamped",
    )

    def __init__(self, leg: dict[str, Any], unified: pd.DataFrame) -> None:
        self.family = leg["family"]
        self.config = dict(leg["config"])
        self.interval_min = int(self.config["interval_min"])
        self.tp_pct = float(self.config["tp_pct"])
        self.sl_pct = float(self.config["sl_pct"])
        # Convert max_hold_h -> bars at the leg's TF.
        self.max_hold_bars = int(round(float(self.config["max_hold_h"]) * 60 / self.interval_min))
        # Position state.
        self.side: int = 0
        self.entry_price: float | None = None
        self.entry_tf_idx: int | None = None
        # Live mode preserves entries across rebuilds via TS, then re-derives idx.
        self.entry_tf_ts: int | None = None
        # Diagnostic flag: set True when refresh_signals had to clamp an
        # entry that fell outside the rolling window. Read+cleared by the
        # outer strategy so it can emit an MFP_ENTRY_LOST audit event.
        self.entry_clamped: bool = False
        # Resample + signals.
        self.refresh_signals(unified)

    def refresh_signals(self, unified: pd.DataFrame) -> None:
        """Re-resample and recompute signal arrays from `unified`.

        Called once at construction (backtest) and on every new 15m bar in
        live mode. When a position is open, ``entry_tf_idx`` is re-derived
        from ``entry_tf_ts`` so the position state survives the rebuild.
        """
        tf_df = _resample_to(unified, self.interval_min)
        self.tf_ts = tf_df["ts"].to_numpy(dtype="int64")
        self.tf_open = tf_df["open"].to_numpy(dtype="float64")
        self.tf_high = tf_df["high"].to_numpy(dtype="float64")
        self.tf_low = tf_df["low"].to_numpy(dtype="float64")
        self.tf_close = tf_df["close"].to_numpy(dtype="float64")
        sig_fn = _SIG_FUNCS[self.family]
        self.long_sig, self.short_sig = sig_fn(tf_df, self.config)
        # Re-derive entry index from preserved entry timestamp (live mode).
        if self.entry_tf_ts is not None and self.tf_ts.size:
            idx = int(np.searchsorted(self.tf_ts, self.entry_tf_ts, side="left"))
            if idx < self.tf_ts.size and int(self.tf_ts[idx]) == int(self.entry_tf_ts):
                self.entry_tf_idx = idx
            else:
                # Entry bar was trimmed out of the rolling window — force a
                # time exit by clamping the index to "very old". Flag this
                # so the outer strategy can emit MFP_ENTRY_LOST.
                self.entry_tf_idx = -max(self.max_hold_bars, 1)
                self.entry_clamped = True


# ---------------------------------------------------------------------------
# Strategy (single class)
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = {
    # If True, only step the strategy on bars marked is_new_bar (matches the
    # vectorised lab semantics). If False, every tick can drive exits/entries.
    "new_bar_only": True,
    # If True, emit a MFP_BAR debug event on every bar with the current
    # target/long_count/short_count/committed_side snapshot. Useful for
    # verifying live behaviour against backtest signals at the cost of one
    # event per bar (~96 events/day on a 15m candle). Can also be enabled
    # globally via env var MFP_DEBUG_BAR_EVENTS=1.
    "debug_bar_events": False,
}

STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {"name": "new_bar_only", "type": "bool", "label": "Step on bar close only"},
    {"name": "debug_bar_events", "type": "bool",
     "label": "Emit per-bar debug event (MFP_BAR)"},
]


class MultiFactorPortfolioStrategy(Strategy):
    """17-leg equal-weight multi-factor portfolio for BTCUSDT-PERP.

    Backtest: requires the 5 parquet files at ``data/perp_meta/`` (15m klines
    + OI + funding + taker + LSR), or equivalents accessible via the
    ``MFP_PARQUET_*`` env-var resolvers.

    Live: requires Redis ZSETs populated by ``scripts/oi_ingestor.py`` and
    ``scripts/perp_meta_ingestor.py``. The strategy seeds from parquet at
    ``initialize()``, gap-fills via providers, then keeps a rolling
    in-memory dataset of the last ~60 days of 15m bars.
    See module docstring for env-var details.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        # Legacy kwargs (`symbol`, `entry_pct`) from older job configs are
        # silently ignored: symbol now comes from ctx.symbol (trade settings)
        # and per-trade sizing is governed by trade-settings max_position /
        # max_order via the runner default sizing path.
        p = {**STRATEGY_PARAMS, **kwargs}
        self.new_bar_only = bool(p["new_bar_only"])
        # Param wins over env var; env var is a fallback for ops debugging
        # without needing to edit the saved job config.
        env_dbg = os.environ.get("MFP_DEBUG_BAR_EVENTS", "").strip().lower()
        self.debug_bar_events = bool(p["debug_bar_events"]) or env_dbg in ("1", "true", "yes", "on")
        self.params = dict(p)

        # Set in initialize() from ctx.symbol; default kept for type stability.
        self.symbol: str = "BTCUSDT"

        self._legs: list[_LegState] = []
        self._committed_side: int = 0  # the side currently held by ctx
        self._mode: str | None = None
        self._last_bar_ts: int = 0
        # Live-mode warmup deferral: ``_initialize_live`` builds the leg
        # objects synchronously but defers the heavy history replay to the
        # async ``post_initialize_async`` so the runner's heartbeat loop
        # stays responsive. These attributes are flipped in
        # ``_initialize_live`` and consumed in ``post_initialize_async``.
        self._needs_warmup: bool = False
        self._live_init_seed_last_ts: int = 0
        # Live-mode rolling state.
        self._unified: pd.DataFrame | None = None
        self._tail_ts_15m: int = 0
        self._oi_provider: Any | None = None
        self._funding_provider: Any | None = None
        self._taker_provider: Any | None = None
        self._lsr_provider: Any | None = None
        # Provider gap accounting (Phase 1.1). Counts NaN-producing reads
        # per kind across the strategy's lifetime so they can be reported
        # via MFP_DATA_GAP. ``_data_gap_warned`` rate-limits per-kind log
        # warnings to once each (to avoid log spam in live mode).
        self._data_gap_counts: dict[str, int] = {
            "oi": 0, "funding": 0, "taker": 0, "lsr": 0,
        }
        self._data_gap_warned: set[str] = set()
        # Diagnostic dump path. When MFP_DUMP_UNIFIED is set, the unified
        # DataFrame is written here once after initialize() completes so
        # backtest vs live data can be diffed offline (Phase 0.1).
        self._dump_path: str = os.environ.get("MFP_DUMP_UNIFIED", "").strip()
        # Bound the in-memory unified dataset. Donchian-192 on 240m needs 32d;
        # 60d gives a safe headroom and keeps memory + CPU per refresh small.
        try:
            self._max_history_15m_bars = int(os.environ.get(
                "MFP_LIVE_HISTORY_BARS_15M",
                str(60 * 24 * 4),  # 60 days * 24h * 4 (15m per hour)
            ))
        except ValueError:
            self._max_history_15m_bars = 60 * 24 * 4
        # Redis-backed state persistence (live mode only). When the runner
        # gets replaced (deploy / SIGTERM) the new replica reads this
        # snapshot and skips the heavy warmup replay. See
        # ``post_initialize_async`` and ``_schedule_persist_state``.
        self._persist_job_id: str | None = None
        self._persist_loop: asyncio.AbstractEventLoop | None = None
        # Restored from snapshot at init time; ``True`` means the on_bar
        # path can skip the next save (state already matches snapshot).
        self._state_restored_from_snapshot: bool = False
        # Snapshot freshness window. Snapshots older than this are
        # ignored; we run a full warmup instead. 30 min is comfortably
        # longer than any plausible deploy or watchdog cycle but short
        # enough that we cannot drift far from backtest semantics.
        try:
            self._state_max_age_sec = int(os.environ.get(
                "MFP_STATE_MAX_AGE_SEC", "1800",
            ))
        except ValueError:
            self._state_max_age_sec = 1800

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

        # Symbol comes from the runner trade-settings via the context, not
        # from strategy params (it would be redundant with the trade panel).
        ctx_symbol = getattr(ctx, "symbol", None)
        if not ctx_symbol:
            raise RuntimeError(
                "MultiFactorPortfolioStrategy: ctx.symbol is required but missing."
            )
        self.symbol = str(ctx_symbol).upper()

        if not _symbol_supported(self.symbol):
            raise ValueError(
                f"MultiFactorPortfolioStrategy: symbol {self.symbol} is not "
                f"enabled. The leg structure is fixed to the BTC-validated "
                f"baseline; per-symbol thresholds must be discovered and "
                f"promoted first "
                f"(scripts/discover_mfp_params.py --symbol {self.symbol})."
            )

        if mode == "live":
            self._initialize_live(ctx)
            return
        if mode != "backtest":
            raise NotImplementedError(
                f"MultiFactorPortfolioStrategy: unsupported context "
                f"{ctx_module}.{ctx_cls}; expected backtest or live."
            )

        unified = _load_unified_dataset(self.symbol)
        if len(unified) == 0:
            raise RuntimeError(
                f"MultiFactorPortfolioStrategy: unified dataset for {self.symbol} is empty. "
                f"Check that the configured parquet sources contain rows for the backtest window."
            )
        seed_last_ts = int(unified["ts"].iloc[-1])

        # Resolve backtest end timestamp from the context (added in this fix).
        # When ctx.end_ts is later than the parquet seed, the backtest engine
        # will feed bars the strategy has no aux data for, so the leg state
        # arrays freeze and no signals fire. Mirror _initialize_live by
        # pre-fetching OI/funding/taker/LSR from Binance REST for the gap
        # window and routing them through the same _gap_fill_to path.
        bar_ms = 15 * 60 * 1000
        ctx_end_ts_raw = int(getattr(ctx, "end_ts", 0) or 0)
        # Round down to the most recently closed 15m bar boundary so the
        # gap-fill targets actual bar opens (parquet ts is open_time).
        backtest_end_ts = (ctx_end_ts_raw // bar_ms) * bar_ms if ctx_end_ts_raw > 0 else 0

        gap_filled_rows = 0
        gap_fill_error: str | None = None
        if backtest_end_ts > seed_last_ts:
            try:
                rest_providers = _build_backtest_rest_providers(
                    self.symbol, seed_last_ts + 1, backtest_end_ts
                )
                self._oi_provider = rest_providers["oi"]
                self._funding_provider = rest_providers["funding"]
                self._taker_provider = rest_providers["taker"]
                self._lsr_provider = rest_providers["lsr"]
                pre_len = len(unified)
                unified = self._gap_fill_to(unified, seed_last_ts, backtest_end_ts)
                gap_filled_rows = int(len(unified) - pre_len)
            except Exception as exc:  # noqa: BLE001 — surface failure but keep backtest runnable
                gap_fill_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[mfp] backtest gap-fill failed; running with stale parquet "
                    "(seed_last_ts=%d, end_ts=%d): %s",
                    seed_last_ts, backtest_end_ts, gap_fill_error,
                )

        self._unified = unified
        self._legs = [_LegState(leg, unified) for leg in resolve_legs(self.symbol)]
        self._committed_side = 0
        self._last_bar_ts = 0
        self._tail_ts_15m = int(unified["ts"].iloc[-1]) if len(unified) else 0
        self._maybe_dump_unified("backtest")
        self._emit_event(ctx, "MFP_INIT", {
            "mode": "backtest",
            "n_legs": len(self._legs),
            "intervals": sorted({leg.interval_min for leg in self._legs}),
            "families": sorted({leg.family for leg in self._legs}),
            "data_rows_15m": int(len(unified)),
            "seed_last_ts": seed_last_ts,
            "backtest_end_ts": backtest_end_ts,
            "gap_filled_rows": gap_filled_rows,
            "gap_fill_error": gap_fill_error,
            "data_gap_counts": dict(self._data_gap_counts),
            "dump_path": self._dump_path or None,
        })

    # ---- live mode internals ----------------------------------------------
    def _initialize_live(self, ctx: StrategyContext) -> None:
        """Initialize for live trading: parquet seed + provider gap-fill + leg build.

        Sequence:
          1. Load parquet seed (same as backtest path).
          2. Set up Redis-backed providers (forces mode='live').
          3. Gap-fill OI/funding/taker/LSR from provider.range() over
             ``[parquet.last_ts, now]``.
          4. Gap-fill 15m klines from Binance REST over the same window.
          5. Trim the unified dataset to the configured rolling window.
          6. Build _LegState per leg.

        Failures here are fatal: live trading without proper history would
        produce wrong signals.
        """
        from indicators.oi_provider import get_oi_provider
        from indicators.perp_meta_provider import (
            get_funding_provider,
            get_taker_provider,
            get_lsr_provider,
        )

        # 1) Parquet seed (covers up to the last refresh of the blob parquets).
        unified = _load_unified_dataset(self.symbol)
        if len(unified) == 0:
            raise RuntimeError(
                "MultiFactorPortfolioStrategy live init: parquet seed is empty for "
                f"{self.symbol}. Cannot bootstrap historical signals."
            )
        seed_last_ts = int(unified["ts"].iloc[-1])
        now_ms = int(__import__("time").time() * 1000)
        # Round 'now' down to the most recently CLOSED 15m bar.
        bar_ms = 15 * 60 * 1000
        latest_closed_open = ((now_ms // bar_ms) - 1) * bar_ms

        # 2) Providers.
        self._oi_provider = get_oi_provider(self.symbol, mode="live")
        self._funding_provider = get_funding_provider(self.symbol, mode="live")
        self._taker_provider = get_taker_provider(self.symbol, mode="live")
        self._lsr_provider = get_lsr_provider(self.symbol, mode="live")

        # 3+4) Gap-fill.
        if latest_closed_open > seed_last_ts:
            unified = self._gap_fill_to(unified, seed_last_ts, latest_closed_open)
        else:
            logger.info("[mfp] seed parquet already at-or-past latest 15m bar; "
                        "no gap to fill")

        # 5) Trim rolling window.
        if len(unified) > self._max_history_15m_bars:
            unified = unified.iloc[-self._max_history_15m_bars:].reset_index(drop=True)

        # 6) Build legs.
        self._unified = unified
        self._legs = [_LegState(leg, unified) for leg in resolve_legs(self.symbol)]
        self._committed_side = 0
        self._last_bar_ts = 0
        self._tail_ts_15m = int(unified["ts"].iloc[-1])

        # 7) Defer the heavy warmup replay to ``post_initialize_async`` so the
        # runner's event loop is free to fire heartbeats while the replay runs
        # in a thread. Without this, ~17 legs × thousands of bars of pure-
        # Python ``_process_leg`` calls would block the loop for several
        # seconds and starve the live heartbeat task, occasionally tripping
        # the stale-heartbeat watchdog and causing a spurious second restart
        # right after a fresh deploy. The MFP_INIT event is also emitted from
        # ``post_initialize_async`` so its payload reflects the post-warmup
        # state (including ``warmup_summary``).
        self._live_init_seed_last_ts = seed_last_ts
        self._needs_warmup = True
        self._maybe_dump_unified("live")
        logger.info(
            "[mfp] live init (pre-warmup): %s legs=%d rows=%d seed_last=%d tail=%d",
            self.symbol, len(self._legs), len(unified),
            seed_last_ts, self._tail_ts_15m,
        )

    async def post_initialize_async(self, ctx: StrategyContext) -> None:
        """Heavy post-init phase that the runner awaits after ``initialize``.

        The runner's heartbeat loop ticks on the same event loop that calls
        ``initialize`` / ``post_initialize_async``. ``_warmup_replay`` is
        CPU-bound pure-Python code (no I/O, no awaits), so running it
        directly here would block the event loop and the heartbeat task
        cannot fire. We hand it to a worker thread via
        ``asyncio.to_thread`` so the loop stays responsive — the heartbeat
        loop continues to run normally during warmup, preventing the
        stale-heartbeat watchdog from re-queuing the job right after a
        fresh deploy.

        Only the live path needs this; the backtest path already builds
        its leg state synchronously inside ``initialize`` and the backtest
        engine has no heartbeat to worry about.
        """
        if self._mode != "live" or not self._needs_warmup:
            return

        # Capture the running event loop so the synchronous ``on_bar``
        # path can fire-and-forget Redis state saves via
        # ``run_coroutine_threadsafe``. ``on_bar`` is dispatched from the
        # same loop thread that runs this coroutine, so the captured
        # reference stays valid for the job's lifetime.
        try:
            self._persist_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._persist_loop = None
        self._persist_job_id = self._extract_job_id(ctx)

        # 1) Try to restore from a fresh Redis snapshot first. When the
        # runner is restarted (deploy / scale event) within ~30 min, the
        # old replica's last on_bar save lets us skip the heavy warmup
        # replay entirely.
        restored = await self._try_restore_state(ctx)
        if restored:
            self._needs_warmup = False
            self._state_restored_from_snapshot = True
            self._emit_event(ctx, "MFP_INIT", {
                "mode": "live",
                "n_legs": len(self._legs),
                "intervals": sorted({leg.interval_min for leg in self._legs}),
                "families": sorted({leg.family for leg in self._legs}),
                "data_rows_15m": int(len(self._unified)) if self._unified is not None else 0,
                "seed_last_ts": self._live_init_seed_last_ts,
                "live_tail_ts": self._tail_ts_15m,
                "data_gap_counts": dict(self._data_gap_counts),
                "dump_path": self._dump_path or None,
                "restored_from_snapshot": True,
            })
            logger.info(
                "[mfp] live init (restored): %s legs=%d tail=%d "
                "committed_side=%d active=%d",
                self.symbol, len(self._legs), self._tail_ts_15m,
                int(self._committed_side),
                sum(1 for leg in self._legs if leg.side != 0),
            )
            return

        # 2) No fresh snapshot → run the full warmup replay in a thread so
        # the event loop stays responsive.
        try:
            warmup_summary = await asyncio.to_thread(self._warmup_replay)
        except Exception as exc:  # noqa: BLE001
            # Warmup failure must not silently leave the strategy half-
            # initialised. Surface it via an event and re-raise so the
            # runner marks the job FAILED rather than running flat.
            self._emit_event(ctx, "MFP_WARMUP_ERROR", {
                "error": f"{type(exc).__name__}: {exc}",
            })
            raise
        self._needs_warmup = False
        self._emit_event(ctx, "MFP_WARMUP", warmup_summary)
        logger.info(
            "[mfp] warmup replay: target=%d long_legs=%d short_legs=%d active=%d",
            warmup_summary.get("target_side", 0),
            warmup_summary.get("long_legs", 0),
            warmup_summary.get("short_legs", 0),
            len(warmup_summary.get("active", [])),
        )

        self._emit_event(ctx, "MFP_INIT", {
            "mode": "live",
            "n_legs": len(self._legs),
            "intervals": sorted({leg.interval_min for leg in self._legs}),
            "families": sorted({leg.family for leg in self._legs}),
            "data_rows_15m": int(len(self._unified)) if self._unified is not None else 0,
            "seed_last_ts": self._live_init_seed_last_ts,
            "live_tail_ts": self._tail_ts_15m,
            "data_gap_counts": dict(self._data_gap_counts),
            "dump_path": self._dump_path or None,
            "restored_from_snapshot": False,
        })
        logger.info(
            "[mfp] live init (post-warmup): %s legs=%d tail=%d active=%d",
            self.symbol, len(self._legs), self._tail_ts_15m,
            len(warmup_summary.get("active", [])),
        )

        # Persist the post-warmup state immediately so subsequent restarts
        # (e.g. a back-to-back deploy) can short-circuit.
        await self._persist_state_async()

    # ---- state persistence -------------------------------------------------
    @staticmethod
    def _extract_job_id(ctx: Any) -> str | None:
        """Pull the LIVE Job UUID out of ``ctx`` (or any wrapper) as a string.

        Returns ``None`` if ``ctx`` exposes no ``job_id``. Used as the
        Redis key so each job has its own snapshot.
        """
        jid = getattr(ctx, "job_id", None)
        if jid is None:
            return None
        return str(jid)

    def _build_snapshot(self) -> dict[str, Any]:
        """Serialise the current rolling state to a JSON-friendly dict."""
        import time as _t
        return {
            "version": 1,
            "saved_at_ms": int(_t.time() * 1000),
            "symbol": self.symbol,
            "tail_ts_15m": int(self._tail_ts_15m),
            "committed_side": int(self._committed_side),
            "legs": [
                {
                    "family": leg.family,
                    "interval_min": int(leg.interval_min),
                    "side": int(leg.side),
                    "entry_price": (
                        float(leg.entry_price) if leg.entry_price is not None else None
                    ),
                    "entry_tf_ts": (
                        int(leg.entry_tf_ts) if leg.entry_tf_ts is not None else None
                    ),
                }
                for leg in self._legs
            ],
        }

    def _restore_from_snapshot(self, snap: dict[str, Any]) -> bool:
        """Apply ``snap`` to the in-memory leg states.

        Returns ``True`` if the snapshot was compatible (same leg shape)
        and was applied. Returns ``False`` if shape mismatched, in which
        case the caller should fall back to a full warmup replay.
        """
        snap_legs = snap.get("legs") or []
        if not isinstance(snap_legs, list) or len(snap_legs) != len(self._legs):
            return False
        # Validate per-leg shape (family + interval_min identity).
        for i, leg in enumerate(self._legs):
            entry = snap_legs[i]
            if not isinstance(entry, dict):
                return False
            if entry.get("family") != leg.family:
                return False
            if int(entry.get("interval_min", -1)) != int(leg.interval_min):
                return False
        # Apply.
        for i, leg in enumerate(self._legs):
            entry = snap_legs[i]
            leg.side = int(entry.get("side", 0) or 0)
            ep = entry.get("entry_price")
            leg.entry_price = float(ep) if ep is not None else None
            ets = entry.get("entry_tf_ts")
            leg.entry_tf_ts = int(ets) if ets is not None else None
            # Re-derive entry_tf_idx from entry_tf_ts via the existing
            # ``refresh_signals`` path so it lines up with the current
            # rolling window.
            leg.refresh_signals(self._unified)
        try:
            self._committed_side = int(snap.get("committed_side", 0) or 0)
        except (TypeError, ValueError):
            self._committed_side = 0
        return True

    async def _try_restore_state(self, ctx: Any) -> bool:
        """Best-effort load + restore. Returns ``True`` on success.

        Returns ``False`` (and leaves state untouched) if:
        - Redis is not configured / unreachable
        - No snapshot exists for this job_id
        - Snapshot is older than ``_state_max_age_sec``
        - Snapshot shape does not match the current ``ALL_LEGS`` layout
        """
        if not self._persist_job_id:
            return False
        try:
            from control.strategy_state import load_state
        except Exception:  # noqa: BLE001
            return False
        try:
            snap = await load_state(self._persist_job_id)
        except Exception:  # noqa: BLE001
            return False
        if not snap:
            return False
        # Freshness check.
        try:
            saved_at_ms = int(snap.get("saved_at_ms", 0) or 0)
        except (TypeError, ValueError):
            saved_at_ms = 0
        if saved_at_ms <= 0:
            return False
        import time as _t
        age_sec = (int(_t.time() * 1000) - saved_at_ms) / 1000.0
        if age_sec > self._state_max_age_sec:
            self._emit_event(ctx, "MFP_RESTORE_SKIPPED", {
                "reason": "snapshot_stale",
                "age_sec": round(age_sec, 1),
                "max_age_sec": int(self._state_max_age_sec),
            })
            return False
        # Symbol guard: snapshot symbol must match (defensive; key is
        # already job-scoped so this should always match).
        if str(snap.get("symbol", "")) != str(self.symbol):
            return False
        if not self._restore_from_snapshot(snap):
            self._emit_event(ctx, "MFP_RESTORE_SKIPPED", {
                "reason": "shape_mismatch",
                "snap_leg_count": len(snap.get("legs") or []),
                "current_leg_count": len(self._legs),
            })
            return False
        long_count = sum(1 for leg in self._legs if leg.side > 0)
        short_count = sum(1 for leg in self._legs if leg.side < 0)
        self._emit_event(ctx, "MFP_RESTORED", {
            "age_sec": round(age_sec, 1),
            "snap_tail_ts": int(snap.get("tail_ts_15m", 0) or 0),
            "live_tail_ts": int(self._tail_ts_15m),
            "committed_side": int(self._committed_side),
            "long_legs": long_count,
            "short_legs": short_count,
        })
        return True

    async def _persist_state_async(self) -> bool:
        """Save the current snapshot to Redis. Best-effort, never raises."""
        if not self._persist_job_id:
            return False
        try:
            from control.strategy_state import save_state
        except Exception:  # noqa: BLE001
            return False
        try:
            return await save_state(self._persist_job_id, self._build_snapshot())
        except Exception:  # noqa: BLE001
            return False

    def _schedule_persist_state(self) -> None:
        """Fire-and-forget Redis save from the synchronous ``on_bar`` path.

        Uses ``run_coroutine_threadsafe`` so this never blocks. Safe to
        call from any thread; safe to call when Redis is unconfigured
        (the inner save coroutine no-ops).
        """
        loop = self._persist_loop
        if loop is None or not loop.is_running():
            return
        if not self._persist_job_id:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._persist_state_async(), loop)
        except Exception:  # noqa: BLE001
            # Scheduling failed (loop closed?) — silently drop; the next
            # on_bar will try again.
            pass

    async def drain_async(self) -> None:
        """Called by the runner on graceful shutdown (SIGTERM/SIGINT).

        The last ``on_bar``-scheduled save may be up to one full bar
        (15 min) old. Force a fresh synchronous save here so the new
        replica's restore sees the up-to-the-second state.
        """
        if self._mode != "live":
            return
        if not self._persist_job_id:
            return
        try:
            saved = await self._persist_state_async()
            logger.info(
                "[mfp] drain_async: final snapshot saved=%s job_id=%s tail=%d "
                "committed_side=%d",
                bool(saved), self._persist_job_id, int(self._tail_ts_15m),
                int(self._committed_side),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[mfp] drain_async failed job_id=%s: %s: %s",
                self._persist_job_id, type(exc).__name__, exc,
            )

    def _warmup_replay(self) -> dict[str, Any]:
        """Replay each leg's TF history through ``_process_leg`` so that
        ``leg.side`` / ``entry_price`` / ``entry_tf_idx`` reflect the same
        position a backtest running to ``now`` would hold.

        Without this, every leg starts flat at job-start (because
        ``_LegState.__init__`` initialises ``side = 0``) and the runner only
        enters on the next *new* signal. Backtest, in contrast, has been
        accumulating leg state for the entire history window, so its
        ``net direction`` at startup is rarely flat — meaning the runner is
        effectively starting from a sparser leg pool than the backtest.

        Replay is safe because:
          * ``_process_leg`` is the same function the backtest uses, so the
            replayed state is bit-identical to the backtest's state at the
            same timestamp.
          * Stale entries (older than ``max_hold_bars``) auto-flatten via
            the time-exit branch inside ``_process_leg`` — no manual
            cutoff needed.
          * No ctx orders are issued here. ``_committed_side`` stays 0;
            the first ``on_bar`` after initialize sees the leg majority
            and routes the entry through the normal ``_reconcile`` ->
            ``ctx.enter_long/short`` path.

        Returns a summary dict (suitable for an MFP_WARMUP audit event)
        with per-leg details of any active position.
        """
        active: list[dict[str, Any]] = []
        if not self._legs:
            return {
                "replayed_legs": 0,
                "long_legs": 0,
                "short_legs": 0,
                "target_side": 0,
                "active": active,
            }

        long_count = 0
        short_count = 0
        for i, leg in enumerate(self._legs):
            n = int(leg.tf_ts.size)
            if n == 0:
                continue
            for tf_idx in range(n):
                self._process_leg(leg, tf_idx)
            if leg.side > 0:
                long_count += 1
            elif leg.side < 0:
                short_count += 1
            if leg.side != 0:
                active.append({
                    "i": i,
                    "family": leg.family,
                    "tf": int(leg.interval_min),
                    "side": int(leg.side),
                    "entry_tf_ts": int(leg.entry_tf_ts) if leg.entry_tf_ts is not None else None,
                    "entry_price": float(leg.entry_price) if leg.entry_price is not None else None,
                })

        target = 1 if long_count > short_count else (-1 if short_count > long_count else 0)
        return {
            "replayed_legs": len(self._legs),
            "long_legs": int(long_count),
            "short_legs": int(short_count),
            "target_side": int(target),
            "active": active,
        }

    def _gap_fill_to(self, unified: pd.DataFrame, last_ts: int,
                     end_ts_inclusive: int) -> pd.DataFrame:
        """Fetch 15m klines + indicator values for ``(last_ts, end_ts_inclusive]``
        and append rows to ``unified``. Returns the appended DataFrame.
        """
        bar_ms = 15 * 60 * 1000
        # Fetch klines (open_time, o, h, l, c) from Binance.
        new_klines = _fetch_klines_15m_sync(
            self.symbol, last_ts + 1, end_ts_inclusive
        )
        if not new_klines:
            return unified
        rows: list[dict[str, Any]] = []
        for k in new_klines:
            ts = int(k[0])
            if ts <= last_ts or ts > end_ts_inclusive:
                continue
            rows.append({
                "ts": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "oi": self._provider_value_at(self._oi_provider, ts, _kind="oi"),
                "funding_rate": self._provider_value_at(self._funding_provider, ts),
                "taker_ratio": self._provider_value_at(self._taker_provider, ts),
                "lsr_count": self._provider_value_at(self._lsr_provider, ts),
            })
        if not rows:
            return unified
        new_df = pd.DataFrame(rows)
        new_df["dt"] = pd.to_datetime(new_df["ts"], unit="ms", utc=True)
        merged = pd.concat([unified, new_df], ignore_index=True)
        merged = (
            merged.sort_values("ts")
            .drop_duplicates("ts", keep="last")
            .reset_index(drop=True)
        )
        logger.info(
            "[mfp] gap-fill: appended %d rows ts=%d..%d",
            len(rows), int(rows[0]["ts"]), int(rows[-1]["ts"]),
        )
        return merged

    def _provider_value_at(self, provider: Any, ts: int, *, _kind: str = "") -> float:
        """Read a single timestamped value from a provider.

        Returns NaN when the provider is missing or raises. NaN reads are
        counted under ``_data_gap_counts[kind]`` and the first occurrence
        per kind triggers a single ``logger.warning`` (no event — at this
        layer we don't have a ctx handle). The outer ``_initialize_live``
        and ``on_bar`` paths surface the accumulated counts in
        ``MFP_INIT`` / ``MFP_DATA_GAP`` events.
        """
        kind = _kind if _kind else "unknown"
        if provider is None:
            self._record_data_gap(kind, "provider is None")
            return float("nan")
        try:
            # OI provider exposes value_at via its private API only on the
            # Redis backend; on the parquet backend it's exposed too. Use
            # whichever method is present.
            fn = getattr(provider, "value_at", None) or getattr(provider, "_value_at")
            v = float(fn(int(ts)))
            if not math.isfinite(v):
                self._record_data_gap(kind, f"non-finite value {v!r}")
            return v
        except Exception as exc:  # noqa: BLE001
            self._record_data_gap(kind, repr(exc))
            return float("nan")

    def _record_data_gap(self, kind: str, reason: str) -> None:
        if kind in self._data_gap_counts:
            self._data_gap_counts[kind] += 1
        else:
            self._data_gap_counts[kind] = 1
        if kind not in self._data_gap_warned:
            self._data_gap_warned.add(kind)
            logger.warning(
                "[mfp] provider gap detected: kind=%s reason=%s "
                "(further occurrences will be counted silently and reported "
                "via MFP_DATA_GAP events)",
                kind, reason,
            )

    def _append_live_bar(self, bar: dict[str, Any], ts: int) -> bool:
        """Append a new 15m bar to ``self._unified``. Returns True if appended."""
        if self._unified is None:
            return False
        if ts <= self._tail_ts_15m:
            return False
        try:
            o = float(bar.get("open"))
            h = float(bar.get("high"))
            lo = float(bar.get("low"))
            c = float(bar.get("close"))
        except (TypeError, ValueError):
            return False
        new_row = {
            "ts": ts,
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "oi": self._provider_value_at(self._oi_provider, ts, _kind="oi"),
            "funding_rate": self._provider_value_at(self._funding_provider, ts),
            "taker_ratio": self._provider_value_at(self._taker_provider, ts),
            "lsr_count": self._provider_value_at(self._lsr_provider, ts),
            "dt": pd.Timestamp(ts, unit="ms", tz="UTC"),
        }
        self._unified = pd.concat(
            [self._unified, pd.DataFrame([new_row])],
            ignore_index=True,
        )
        if len(self._unified) > self._max_history_15m_bars:
            self._unified = self._unified.iloc[-self._max_history_15m_bars:].reset_index(drop=True)
        self._tail_ts_15m = ts
        return True

    def _refresh_legs_for_live(self) -> None:
        """Rebuild signal arrays for every leg from the updated unified df.

        Position state is preserved via each leg's ``entry_tf_ts``.
        """
        if self._unified is None:
            return
        for leg in self._legs:
            leg.refresh_signals(self._unified)

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if self.new_bar_only and not bool(bar.get("is_new_bar", True)):
            return
        ts = _bar_ts(bar)
        if ts <= 0 or ts == self._last_bar_ts:
            return
        self._last_bar_ts = ts

        # Live mode: append the new 15m bar to the rolling unified dataset
        # and refresh signal arrays for every leg before per-leg processing.
        # Capture pre-refresh gap counts so we can detect any newly-NaN
        # provider reads triggered by ``_append_live_bar`` and emit a
        # single ``MFP_DATA_GAP`` event per bar with the delta.
        gap_before = (
            dict(self._data_gap_counts) if self._mode == "live" else None
        )
        if self._mode == "live" and ts > self._tail_ts_15m:
            if self._append_live_bar(bar, ts):
                self._refresh_legs_for_live()
        if gap_before is not None:
            delta = {
                k: int(self._data_gap_counts.get(k, 0) - gap_before.get(k, 0))
                for k in self._data_gap_counts
            }
            if any(v > 0 for v in delta.values()):
                self._emit_event(ctx, "MFP_DATA_GAP", {
                    "ts": ts,
                    "delta": delta,
                    "total": dict(self._data_gap_counts),
                })

        # Surface any entries that were clamped out of the rolling window
        # during refresh_signals. Backtest never clamps; only live mode
        # with a 60-day rolling window can. Once reported, the flag is
        # cleared so we don't double-emit.
        clamped: list[dict[str, Any]] = []
        for i, leg in enumerate(self._legs):
            if leg.entry_clamped:
                clamped.append({
                    "i": i,
                    "family": leg.family,
                    "tf": int(leg.interval_min),
                    "side": int(leg.side),
                    "entry_tf_ts": int(leg.entry_tf_ts) if leg.entry_tf_ts is not None else None,
                })
                leg.entry_clamped = False
        if clamped:
            self._emit_event(ctx, "MFP_ENTRY_LOST", {"ts": ts, "legs": clamped})

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

        # Snapshot committed_side BEFORE _reconcile so the debug event can
        # report whether this bar caused a position change.
        prev_committed = self._committed_side

        # Reconcile ctx position with target. Wrap in try/finally so any
        # exception raised by the engine's risk checks (e.g. portfolio
        # exposure limit) still lets us emit the per-bar debug event and
        # persist the post-bar state to Redis. Without this, the very
        # first bar that fails risk checks would silently freeze the
        # snapshot (no MFP_BAR, no save) until the position eventually
        # clears.
        reconcile_exc: BaseException | None = None
        try:
            self._reconcile(ctx, target, long_count, short_count, ts)
        except BaseException as exc:  # noqa: BLE001
            reconcile_exc = exc

        # Per-bar debug snapshot (off by default). Useful for live trading
        # to verify that signals are firing at the expected cadence and
        # that ctx position tracks the leg majority.
        if self.debug_bar_events:
            active_legs = [
                {"i": i, "family": leg.family, "tf": leg.interval_min, "side": int(leg.side)}
                for i, leg in enumerate(self._legs) if leg.side != 0
            ]
            self._emit_event(ctx, "MFP_BAR", {
                "ts": ts,
                "target": int(target),
                "long_legs": long_count,
                "short_legs": short_count,
                "committed_side": int(self._committed_side),
                "prev_side": int(prev_committed),
                "changed": bool(prev_committed != self._committed_side),
                "active_legs": active_legs,
                "reconcile_error": (
                    str(reconcile_exc) if reconcile_exc is not None else None
                ),
            })

        # Persist the post-on_bar state to Redis so a runner restart can
        # restore us without a warmup replay. Throttled implicitly by the
        # 15m bar cadence (~96 saves/day per symbol).
        if self._mode == "live":
            self._schedule_persist_state()

        # Re-raise the reconcile exception (if any) so the engine still
        # records STRATEGY_ERROR for visibility. The persist+event above
        # have already fired by this point.
        if reconcile_exc is not None:
            raise reconcile_exc

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
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None; leg.entry_tf_ts = None
                    return
                if lo <= sl_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None; leg.entry_tf_ts = None
                    return
                if h >= tp_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None; leg.entry_tf_ts = None
                    return
            else:  # short
                tp_level = ep * (1.0 - leg.tp_pct)
                sl_level = ep * (1.0 + leg.sl_pct)
                if o >= sl_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None; leg.entry_tf_ts = None
                    return
                if h >= sl_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None; leg.entry_tf_ts = None
                    return
                if lo <= tp_level:
                    leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None; leg.entry_tf_ts = None
                    return
            # Time exit
            if (tf_idx - leg.entry_tf_idx) >= leg.max_hold_bars:
                leg.side = 0; leg.entry_price = None; leg.entry_tf_idx = None; leg.entry_tf_ts = None
                return

        # 2) Entry on this closed bar's signal (edge-triggered).
        if leg.side == 0 and tf_idx < len(leg.long_sig):
            if bool(leg.long_sig[tf_idx]):
                leg.side = 1
                leg.entry_price = float(leg.tf_close[tf_idx])
                leg.entry_tf_idx = tf_idx
                leg.entry_tf_ts = int(leg.tf_ts[tf_idx])
            elif bool(leg.short_sig[tf_idx]):
                leg.side = -1
                leg.entry_price = float(leg.tf_close[tf_idx])
                leg.entry_tf_idx = tf_idx
                leg.entry_tf_ts = int(leg.tf_ts[tf_idx])

    def _reconcile(self, ctx: StrategyContext, target: int, long_count: int,
                    short_count: int, ts: int) -> None:
        # Defensive sync (Phase 4.2): if the runner closed the position out
        # from under us (e.g. runner-level STOP_LOSS, daily-loss-limit,
        # external manual flatten), our cached ``_committed_side`` would
        # disagree with the real ctx state and ``_reconcile`` would refuse
        # to re-enter on subsequent leg-majority bars (``target == cur``
        # short-circuit). Sync from ctx.position before every reconcile so
        # we always reflect ground truth. This is a no-op when nothing
        # external happened.
        pos = getattr(ctx, "position", None)
        if pos is not None:
            try:
                size = float(getattr(pos, "size", 0.0) or 0.0)
            except (TypeError, ValueError):
                size = 0.0
            if abs(size) < 1e-12:
                actual = 0
            else:
                actual = 1 if size > 0 else -1
            if actual != self._committed_side:
                self._emit_event(ctx, "MFP_CTX_RESYNC", {
                    "ts": ts,
                    "cached_side": int(self._committed_side),
                    "actual_side": int(actual),
                })
                self._committed_side = actual
        cur = self._committed_side
        if target == cur:
            return

        # Case A: FLIP (long <-> short). Live must close AND re-enter the
        # opposite direction in this same bar. The legacy "close_position
        # then enter_*" sequence silently dropped the entry in live because
        # ``close_position`` leaves the position non-zero and sets
        # ``_order_inflight`` until the close fill arrives, so the
        # immediately-following ``enter_short``/``enter_long`` was rejected
        # by the ``position.size != 0`` and ``_order_inflight`` guards.
        # ``ctx.flip_position`` queues the entry to fire right after the
        # close fill, restoring backtest semantics in live.
        if cur != 0 and target != 0 and cur != target:
            prev_label = "long" if cur > 0 else "short"
            next_label = "long" if target > 0 else "short"
            close_reason = (
                f"MFP: net direction flip ({prev_label}->{next_label})"
            )
            if target == 1:
                entry_reason = f"MFP: net long ({long_count}>{short_count})"
            else:
                entry_reason = f"MFP: net short ({short_count}>{long_count})"

            flip_fn = getattr(ctx, "flip_position", None)
            if callable(flip_fn):
                try:
                    flip_fn(
                        target_side=int(target),
                        close_reason=close_reason,
                        entry_reason=entry_reason,
                    )
                except Exception:  # noqa: BLE001
                    pass
            else:
                # Backward-compat for any context that hasn't been upgraded
                # yet. Still suffers from the live race condition described
                # above; included only so older callers don't crash.
                try:
                    ctx.close_position(reason=close_reason)
                except Exception:  # noqa: BLE001
                    pass
                if target == 1:
                    ctx.enter_long(reason=entry_reason)
                else:
                    ctx.enter_short(reason=entry_reason)

            # Preserve the existing two-event shape so downstream consumers
            # (logs, evaluators) don't need to learn a new event name.
            self._emit_event(ctx, "MFP_FLAT", {
                "ts": ts, "target": int(target), "prev_side": int(cur),
                "committed_side": 0,
                "long_legs": long_count, "short_legs": short_count,
                "kind": "flip",
            })
            if target == 1:
                self._committed_side = 1
                self._emit_event(ctx, "MFP_ENTER_LONG", {
                    "ts": ts, "target": 1, "prev_side": int(cur),
                    "committed_side": 1,
                    "long_legs": long_count, "short_legs": short_count,
                })
            else:
                self._committed_side = -1
                self._emit_event(ctx, "MFP_ENTER_SHORT", {
                    "ts": ts, "target": -1, "prev_side": int(cur),
                    "committed_side": -1,
                    "long_legs": long_count, "short_legs": short_count,
                })
            return

        # Case B: pure FLAT (close current position, no re-entry).
        if cur != 0 and target == 0:
            close_reason = f"MFP: net flat ({long_count}={short_count})"
            try:
                ctx.close_position(reason=close_reason)
            except Exception:  # noqa: BLE001
                pass
            self._committed_side = 0
            self._emit_event(ctx, "MFP_FLAT", {
                "ts": ts, "target": 0, "prev_side": int(cur),
                "committed_side": 0,
                "long_legs": long_count, "short_legs": short_count,
                "kind": "flat",
            })
            return

        # Case C: pure ENTRY from flat. Sizing is delegated entirely to the
        # runner trade-settings (max_position/max_order); no per-strategy
        # entry_pct override.
        if target == 1:
            ctx.enter_long(reason=f"MFP: net long ({long_count}>{short_count})")
            self._committed_side = 1
            self._emit_event(ctx, "MFP_ENTER_LONG", {
                "ts": ts, "target": int(target), "prev_side": int(cur),
                "committed_side": 1,
                "long_legs": long_count, "short_legs": short_count,
            })
        elif target == -1:
            ctx.enter_short(reason=f"MFP: net short ({short_count}>{long_count})")
            self._committed_side = -1
            self._emit_event(ctx, "MFP_ENTER_SHORT", {
                "ts": ts, "target": int(target), "prev_side": int(cur),
                "committed_side": -1,
                "long_legs": long_count, "short_legs": short_count,
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

    def _maybe_dump_unified(self, mode_label: str) -> None:
        """If ``MFP_DUMP_UNIFIED`` is set, write the unified DataFrame to that
        path (parquet) so backtest and live runs can be diff-compared.

        Path may contain ``{mode}`` / ``{symbol}`` / ``{ts}`` placeholders.
        Failures are swallowed (diagnostic-only).
        """
        if not self._dump_path or self._unified is None or len(self._unified) == 0:
            return
        try:
            import time as _t
            path = self._dump_path.format(
                mode=mode_label,
                symbol=self.symbol,
                ts=int(_t.time()),
            )
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._unified.to_parquet(path)
            logger.info("[mfp] dumped unified df (%s) -> %s (rows=%d)",
                        mode_label, path, len(self._unified))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[mfp] failed to dump unified df: %s", exc)


def _bar_ts(bar: dict[str, Any]) -> int:
    for key in ("bar_timestamp", "timestamp"):
        try:
            v = int(bar.get(key, 0) or 0)
        except Exception:  # noqa: BLE001
            v = 0
        if v > 0:
            return v
    return 0
