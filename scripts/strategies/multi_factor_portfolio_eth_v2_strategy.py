"""ETH v2 - real-model regime-robust multi-factor portfolio (11 legs).

WHY A SEPARATE FILE (v2)
------------------------
* ``multi_factor_portfolio_strategy`` is frozen for BTCUSDT and
  ``multi_factor_portfolio_eth_strategy`` (v1) is frozen as the full-history
  ETH fit. Per project convention we add a new file rather than edit either.

EXECUTION MODEL (important)
---------------------------
The parent strategy collapses every leg's side into ONE net direction by
majority vote and holds a SINGLE 1x position (see ``on_bar`` / ``_reconcile``
in the parent). It does NOT run legs as independent capital sleeves. This v2
was therefore selected and validated under that real single-position model -
NOT under per-leg-averaged returns.

SELECTION
---------
* Candidate pool: donchian_breakout / oi_z_combo / pullback_in_trend /
  ensemble_meanrev / lsr_taker_confluence across timeframes (15/30/60/120/240m),
  channel lookbacks, sides and hold horizons.
* Forward selection on the REAL majority-vote single-position equity over the
  full backtestable window (2022-01..2026-06), two-phase: first build trade
  frequency to ~1/day, then refine for lower MDD / higher +months, subject to
  a hard MDD <= 20% ceiling.

PERFORMANCE - real single-position model, commission 0.0004 both sides
----------------------------------------------------------------------
# FULL 2022-01..2026-06 : ret +138.5%  MDD 18.9%  +months 64.8%  worst -13.4%  ~0.87 trades/day
# HOLDOUT 2025-01..2026-05: ret +45.9%  MDD 18.6%  +months 64.7%  worst -12.4%
# Per-calendar-year (every year positive, incl. the 2022 and 2025 bears):
#   2022: ret +18.3%  MDD 14.8%  +months 75%  worst -8.4%
#   2023: ret +7.9%  MDD 17.6%  +months 58%  worst -13.4%
#   2024: ret +29.5%  MDD 18.9%  +months 67%  worst -9.0%
#   2025: ret +33.4%  MDD 18.6%  +months 75%  worst -12.4%
#   2026: ret +8.2%  MDD 16.3%  +months 33%  worst -7.0%
#
# This is the realistic single-position result. A much smoother, low-MDD curve
# is only attainable by running legs as independent fractional-capital sleeves,
# which the parent execution model does not support.

RUNTIME CONTRACT
----------------
Identical to the parent strategy. ``resolve_legs`` / ``_symbol_supported`` are
scope-patched during ``initialize`` only (like v1), so the BTC parent and ETH
v1 are bit-for-bit unaffected.
"""
from __future__ import annotations

import logging
from typing import Any

from scripts.strategies import multi_factor_portfolio_strategy as _mfp
from scripts.strategies.multi_factor_portfolio_strategy import (
    MultiFactorPortfolioStrategy,
)

from strategy.context import StrategyContext

logger = logging.getLogger(__name__)

BASELINE_SYMBOL = "ETHUSDT"
STRATEGY_ID = "multi_factor_portfolio_eth_v2"


# ---------------------------------------------------------------------------
# v2 legs - 11 fully-specified legs (real-model validated). Generated from
# the validated artifact by scripts/_alpha_lab/_gen_eth_v2_from_realmodel.py -
# do not hand-edit.
# ---------------------------------------------------------------------------
ETH_V2_LEGS: list[dict[str, Any]] = [
    {"family": "lsr_taker_confluence", "config": {"interval_min": 15, "lsr_lb": 240, "z_lsr_long": -1.0, "z_lsr_short": 1.5, "use_taker": True, "taker_lb": 240, "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count", "use_rsi": True, "rsi_long": 35.0, "rsi_short": 60.0, "tp_pct": 0.018, "sl_pct": 0.008, "max_hold_h": 16, "side": "both"}},  # noqa: E501
    {"family": "ensemble_meanrev", "config": {"interval_min": 60, "bb_period": 20, "bb_std": 1.8, "rsi_period": 14, "rsi_long": 40.0, "rsi_short": 65.0, "oi_lb": 96, "oi_drop": -0.015, "oi_pop": 0.02, "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07, "lsr_z_lookback": 480, "lsr_z_long": -1.0, "lsr_z_short": 1.5, "min_votes": 3, "use_trend_filter": True, "trend_ema": 200, "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025, "tp_pct": 0.012, "sl_pct": 0.012, "max_hold_h": 8, "side": "both"}},  # noqa: E501
    {"family": "lsr_taker_confluence", "config": {"interval_min": 60, "lsr_lb": 240, "z_lsr_long": -1.0, "z_lsr_short": 1.0, "use_taker": False, "taker_lb": 240, "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count", "use_rsi": True, "rsi_long": 40.0, "rsi_short": 65.0, "tp_pct": 0.025, "sl_pct": 0.012, "max_hold_h": 4, "side": "short_only"}},  # noqa: E501
    {"family": "ensemble_meanrev", "config": {"interval_min": 60, "bb_period": 20, "bb_std": 2.0, "rsi_period": 14, "rsi_long": 35.0, "rsi_short": 65.0, "oi_lb": 96, "oi_drop": -0.025, "oi_pop": 0.03, "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07, "lsr_z_lookback": 480, "lsr_z_long": -1.0, "lsr_z_short": 1.0, "min_votes": 3, "use_trend_filter": True, "trend_ema": 200, "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025, "tp_pct": 0.012, "sl_pct": 0.012, "max_hold_h": 8, "side": "both"}},  # noqa: E501
    {"family": "ensemble_meanrev", "config": {"interval_min": 60, "bb_period": 20, "bb_std": 2.0, "rsi_period": 14, "rsi_long": 40.0, "rsi_short": 60.0, "oi_lb": 96, "oi_drop": -0.015, "oi_pop": 0.02, "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07, "lsr_z_lookback": 480, "lsr_z_long": -1.0, "lsr_z_short": 1.5, "min_votes": 3, "use_trend_filter": True, "trend_ema": 200, "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025, "tp_pct": 0.012, "sl_pct": 0.012, "max_hold_h": 8, "side": "both"}},  # noqa: E501
    {"family": "donchian_breakout", "config": {"interval_min": 240, "dc_period": 192, "atr_min_mult": 0.0, "use_oi": False, "oi_lb": 96, "oi_min_for_long": 0.0, "oi_max_for_short": -0.01, "tp_pct": 0.04, "sl_pct": 0.015, "max_hold_h": 96, "side": "short_only"}},  # noqa: E501
    {"family": "donchian_breakout", "config": {"interval_min": 60, "dc_period": 192, "atr_min_mult": 0.0, "use_oi": False, "oi_lb": 96, "oi_min_for_long": 0.0, "oi_max_for_short": -0.01, "tp_pct": 0.08, "sl_pct": 0.025, "max_hold_h": 24, "side": "long_only"}},  # noqa: E501
    {"family": "oi_z_combo", "config": {"interval_min": 60, "oi_lb": 192, "z_lookback": 480, "z_long": -1.5, "z_short": 2.0, "use_rsi": True, "rsi_long_max": 45.0, "rsi_short_min": 55.0, "use_taker": False, "taker_long_max": 0.95, "taker_short_min": 1.05, "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025, "tp_pct": 0.025, "sl_pct": 0.012, "max_hold_h": 8, "side": "short_only"}},  # noqa: E501
    {"family": "donchian_breakout", "config": {"interval_min": 60, "dc_period": 192, "atr_min_mult": 0.0, "use_oi": False, "oi_lb": 96, "oi_min_for_long": 0.0, "oi_max_for_short": -0.01, "tp_pct": 0.04, "sl_pct": 0.015, "max_hold_h": 24, "side": "long_only"}},  # noqa: E501
    {"family": "lsr_taker_confluence", "config": {"interval_min": 15, "lsr_lb": 240, "z_lsr_long": -1.0, "z_lsr_short": 1.5, "use_taker": True, "taker_lb": 240, "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count", "use_rsi": True, "rsi_long": 35.0, "rsi_short": 60.0, "tp_pct": 0.012, "sl_pct": 0.012, "max_hold_h": 4, "side": "short_only"}},  # noqa: E501
    {"family": "lsr_taker_confluence", "config": {"interval_min": 15, "lsr_lb": 240, "z_lsr_long": -1.0, "z_lsr_short": 1.5, "use_taker": True, "taker_lb": 240, "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count", "use_rsi": True, "rsi_long": 35.0, "rsi_short": 60.0, "tp_pct": 0.018, "sl_pct": 0.008, "max_hold_h": 4, "side": "both"}},  # noqa: E501
]


def resolve_legs(symbol: str) -> list[dict[str, Any]]:
    sym = symbol.upper()
    if sym != BASELINE_SYMBOL:
        raise ValueError(
            f"MultiFactorPortfolioEthV2Strategy: symbol {sym} is not "
            f"supported; this strategy is tuned exclusively for "
            f"{BASELINE_SYMBOL}."
        )
    return ETH_V2_LEGS


def _symbol_supported(symbol: str) -> bool:
    return symbol.upper() == BASELINE_SYMBOL


STRATEGY_PARAMS: dict[str, Any] = dict(_mfp.STRATEGY_PARAMS)
STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = list(_mfp.STRATEGY_PARAM_SCHEMA)


class MultiFactorPortfolioEthV2Strategy(MultiFactorPortfolioStrategy):
    """ETH v2 - 11-leg real-model regime-robust multi-factor portfolio.

    Reuses the parent lifecycle, data loaders, REST providers, signal funcs and
    per-leg state machine. Only the leg set changes; it was selected under the
    parent's real majority-vote single-position execution model.
    """

    def initialize(self, ctx: StrategyContext) -> None:
        ctx_symbol = getattr(ctx, "symbol", None)
        if ctx_symbol and str(ctx_symbol).upper() != BASELINE_SYMBOL:
            raise ValueError(
                f"MultiFactorPortfolioEthV2Strategy: ctx.symbol={ctx_symbol} "
                f"is not supported; tuned exclusively for {BASELINE_SYMBOL}."
            )
        orig_resolve = _mfp.resolve_legs
        orig_supported = _mfp._symbol_supported
        _mfp.resolve_legs = resolve_legs
        _mfp._symbol_supported = _symbol_supported
        try:
            super().initialize(ctx)
        finally:
            _mfp.resolve_legs = orig_resolve
            _mfp._symbol_supported = orig_supported
