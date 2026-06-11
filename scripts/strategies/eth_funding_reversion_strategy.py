"""ETHUSDT 15m — FUNDING-rate crowding reversion (leg 1 of 5).

Mechanism (orthogonal data source: perpetual FUNDING rate / carry):
  When funding is abnormally HIGH vs its trailing window, longs are crowded and
  paying to hold -> fade SHORT; abnormally LOW -> shorts crowded -> fade LONG.
  Here we deploy the LONG-only side (funding spikes negative -> mean-revert up),
  which was the robust, generalising half over the full ETH history.

Config (a-priori, never re-tuned): z_win=384, z_thr=1.0, max_hold=192 (48h),
  side=long, lb=96.  Pure time exit, no TP/SL.

Validated single-position metrics (ETHUSDT 15m, 10bp round-trip cost):
  IN-SAMPLE (full-window tuned) : +64.7% / MDD 28.6% / Calmar 0.39 / 0.23 tpd
  STRICT-OOS, fixed a-priori    : +107.7% / MDD 35.1% / Calmar 0.53 / 0.20 tpd
  STRICT-OOS, per-fold WFO      : -9.7%  / MDD 63.9% (re-tuning destroys it;
    reversion must use FIXED params -- this leg is held constant in production)

This is one leg of a 5-strategy uncorrelated portfolio (funding / taker-flow /
open-interest / top-trader account-LSR / top-trader position-LSR).  Run all five
at equal risk: the blended portfolio is +70.4% / MDD 6.3% / 1.09 trades-day /
Calmar 2.48, positive in every calendar year.  Deploy this leg on its own only
as a diversifier -- its standalone drawdown is high.

Live data: served by ``indicators.perp_meta_provider.get_funding_provider``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eth_crowd_reversion_base import (  # noqa: E402,F401
    CrowdReversionStrategy as _CrowdReversionBase, STRATEGY_PARAM_SCHEMA,
)

PRESET: dict[str, Any] = {
    "source": "funding", "z_win": 384, "z_thr": 1.0,
    "max_hold_bars": 192, "side": "long", "lb": 96, "sl_pct": None,
}

STRATEGY_PARAMS: dict[str, Any] = dict(PRESET)


class EthFundingReversionStrategy(_CrowdReversionBase):
    """Funding-rate crowding reversion (LONG fade of negative funding spikes)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**{**PRESET, **kwargs})
