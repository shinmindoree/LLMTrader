"""ETH v3 - low-MDD steady-uptrend multi-factor portfolio (4 legs).

WHY A SEPARATE FILE (v3)
------------------------
* ``multi_factor_portfolio_strategy`` (BTC), ``..._eth_strategy`` (v1) and
  ``..._eth_v2_strategy`` are all frozen. Per project convention we add a new
  file rather than edit any of them.
* v3 targets a LOWER drawdown than v2 (which ran ~19% MDD at ~0.9 trades/day),
  trading some frequency for a smoother, steadier equity curve.

EXECUTION MODEL
---------------
The parent strategy collapses every leg's side into ONE net direction by
majority vote and holds a SINGLE 1x position (parent ``on_bar`` /
``_reconcile``). v3 was selected and validated under that real single-position
model - NOT under per-leg-averaged returns.

SELECTION (scripts/_alpha_lab/_eth_v3_random.py)
------------------------------------------------
* Candidate pool: donchian_breakout / oi_z_combo / pullback_in_trend /
  ensemble_meanrev / lsr_taker_confluence across timeframes (15/30/60/120/240m),
  lookbacks, sides and (short) hold horizons.
* Random-restart greedy on the REAL majority-vote single-position equity over
  2022-01..2026-06, primary objective MINIMISE MDD, hard constraints:
  trade frequency in (0, 1]/day AND every calendar year positive. Best of many
  restarts kept (lower-MDD bands preferred, then steadier +months).

PERFORMANCE - real single-position model, commission 0.0004 both sides
----------------------------------------------------------------------
# FULL 2022-01..2026-06 : ret +49.2%  MDD 16.1%  +months 51.9%  worst -8.0%  ~0.43 trades/day
# HOLDOUT 2025-01..2026-05: ret +18.2%  MDD 16.1%  +months 58.8%  worst -8.0%
# Per-calendar-year (every year positive, incl. the 2022 and 2025 bears):
#   2022: ret +10.5%  MDD 12.2%  +months 55%  worst -4.9%
#   2023: ret +5.3%  MDD 12.7%  +months 58%  worst -5.9%
#   2024: ret +8.5%  MDD 14.8%  +months 42%  worst -4.3%
#   2025: ret +11.5%  MDD 16.1%  +months 58%  worst -7.1%
#   2026: ret +6.1%  MDD 9.8%  +months 60%  worst -8.0%
#
# vs v2 (MDD ~19%): v3 trades fewer times (~0.43/day) for a lower MDD and
# steadier monthly profile. A much smoother sub-5%% MDD curve is only attainable
# with independent fractional-capital sleeves, which the parent does not support.

RUNTIME CONTRACT
----------------
Identical to the parent strategy. ``resolve_legs`` / ``_symbol_supported`` are
scope-patched during ``initialize`` only (like v1/v2), so the BTC parent and
the ETH v1/v2 strategies are bit-for-bit unaffected.
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
STRATEGY_ID = "multi_factor_portfolio_eth_v3"


# ---------------------------------------------------------------------------
# v3 legs - 4 fully-specified legs (real-model validated). Generated from
# the validated artifact by scripts/_alpha_lab/_gen_eth_v3_from_realmodel.py -
# do not hand-edit.
# ---------------------------------------------------------------------------
ETH_V3_LEGS: list[dict[str, Any]] = [
    {"family": "ensemble_meanrev", "config": {"interval_min": 15, "bb_period": 20, "bb_std": 2.0, "rsi_period": 14, "rsi_long": 40.0, "rsi_short": 70.0, "oi_lb": 96, "oi_drop": -0.025, "oi_pop": 0.03, "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07, "lsr_z_lookback": 480, "lsr_z_long": -1.5, "lsr_z_short": 1.0, "min_votes": 3, "use_trend_filter": True, "trend_ema": 200, "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025, "tp_pct": 0.025, "sl_pct": 0.012, "max_hold_h": 16, "side": "both"}},  # noqa: E501
    {"family": "donchian_breakout", "config": {"interval_min": 120, "dc_period": 192, "atr_min_mult": 0.0, "use_oi": False, "oi_lb": 96, "oi_min_for_long": 0.0, "oi_max_for_short": -0.01, "tp_pct": 0.04, "sl_pct": 0.015, "max_hold_h": 72, "side": "long_only"}},  # noqa: E501
    {"family": "ensemble_meanrev", "config": {"interval_min": 15, "bb_period": 20, "bb_std": 2.0, "rsi_period": 14, "rsi_long": 30.0, "rsi_short": 70.0, "oi_lb": 96, "oi_drop": -0.025, "oi_pop": 0.03, "taker_lb": 96, "taker_long_max": 0.93, "taker_short_min": 1.07, "lsr_z_lookback": 480, "lsr_z_long": -1.5, "lsr_z_short": 1.0, "min_votes": 3, "use_trend_filter": True, "trend_ema": 200, "use_atr_filter": True, "atr_min_pct": 0.0025, "atr_max_pct": 0.025, "tp_pct": 0.025, "sl_pct": 0.008, "max_hold_h": 16, "side": "short_only"}},  # noqa: E501
    {"family": "lsr_taker_confluence", "config": {"interval_min": 60, "lsr_lb": 240, "z_lsr_long": -1.0, "z_lsr_short": 1.0, "use_taker": False, "taker_lb": 240, "z_taker_long": -1.0, "z_taker_short": 1.0, "lsr_col": "lsr_count", "use_rsi": True, "rsi_long": 40.0, "rsi_short": 65.0, "tp_pct": 0.025, "sl_pct": 0.012, "max_hold_h": 8, "side": "long_only"}},  # noqa: E501
]


def resolve_legs(symbol: str) -> list[dict[str, Any]]:
    sym = symbol.upper()
    if sym != BASELINE_SYMBOL:
        raise ValueError(
            f"MultiFactorPortfolioEthV3Strategy: symbol {sym} is not "
            f"supported; this strategy is tuned exclusively for "
            f"{BASELINE_SYMBOL}."
        )
    return ETH_V3_LEGS


def _symbol_supported(symbol: str) -> bool:
    return symbol.upper() == BASELINE_SYMBOL


STRATEGY_PARAMS: dict[str, Any] = dict(_mfp.STRATEGY_PARAMS)
STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = list(_mfp.STRATEGY_PARAM_SCHEMA)


class MultiFactorPortfolioEthV3Strategy(MultiFactorPortfolioStrategy):
    """ETH v3 - 4-leg low-MDD steady-uptrend multi-factor portfolio.

    Reuses the parent lifecycle, data loaders, REST providers, signal funcs and
    per-leg state machine. Only the leg set changes; it was selected under the
    parent's real majority-vote single-position execution model to minimise
    drawdown while keeping every calendar year positive at <=1 trade/day.
    """

    def initialize(self, ctx: StrategyContext) -> None:
        ctx_symbol = getattr(ctx, "symbol", None)
        if ctx_symbol and str(ctx_symbol).upper() != BASELINE_SYMBOL:
            raise ValueError(
                f"MultiFactorPortfolioEthV3Strategy: ctx.symbol={ctx_symbol} "
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
