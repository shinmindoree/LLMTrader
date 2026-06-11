"""ETHUSDT 15m — OPEN-INTEREST build-up reversion (leg 3 of 5).

Mechanism (orthogonal data source: aggregate OPEN INTEREST):
  A fast rise in OI over ``lb`` bars (z>thr) marks crowded fresh leverage that
  tends to unwind.  Price confirms which side is crowded:
      OI up + price up   -> crowded longs  -> fade SHORT
      OI up + price down -> crowded shorts -> fade LONG
  We deploy the LONG-only side (OI build-up into a sell-off -> mean-revert up).

Config (a-priori, never re-tuned): z_win=384, z_thr=1.5, max_hold=96 (24h),
  side=long, lb=96.  Pure time exit, no TP/SL.

Validated single-position metrics (ETHUSDT 15m, 10bp round-trip cost):
  IN-SAMPLE (full-window tuned) : +28.7% / MDD 12.8% / Calmar 0.50 / 0.05 tpd
  STRICT-OOS, fixed a-priori    : +22.5% / MDD 24.6% / Calmar 0.20 / 0.16 tpd
  STRICT-OOS, per-fold WFO      : -38.1% / MDD 47.2% (re-tuning destroys it)
  This is the weakest standalone leg but the BEST diversifier (near-zero/negative
  monthly-return correlation with every other leg), which is why it earns the
  largest risk-parity weight and roughly halves the portfolio drawdown.

One leg of a 5-strategy uncorrelated portfolio (funding / taker-flow / open-
interest / top-trader account-LSR / top-trader position-LSR).  Blended at equal
risk the portfolio is +70.4% / MDD 6.3% / 1.09 trades-day / Calmar 2.48,
positive every calendar year.

Live data: served by ``indicators.oi_provider.get_oi_provider``.
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
    "source": "oi", "z_win": 384, "z_thr": 1.5,
    "max_hold_bars": 96, "side": "long", "lb": 96, "sl_pct": None,
}

STRATEGY_PARAMS: dict[str, Any] = dict(PRESET)


class EthOiBuildupReversionStrategy(_CrowdReversionBase):
    """Open-interest build-up reversion with price confirmation (LONG side)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**{**PRESET, **kwargs})
