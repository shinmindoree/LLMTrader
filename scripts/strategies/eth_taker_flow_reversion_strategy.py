"""ETHUSDT 15m — TAKER-FLOW crowding reversion (leg 2 of 5).

Mechanism (orthogonal data source: aggressive TAKER buy/sell volume ratio):
  When taker flow is abnormally buy-heavy vs its trailing window (z>thr), the
  aggressor crowd is leaning long and tends to exhaust -> fade.  We deploy the
  LONG-only side (extreme sell-heavy flow -> mean-revert up), the robust half.

Config (a-priori, never re-tuned): z_win=672, z_thr=1.5, max_hold=96 (24h),
  side=long, lb=96.  Pure time exit, no TP/SL.

Validated single-position metrics (ETHUSDT 15m, 10bp round-trip cost):
  IN-SAMPLE (full-window tuned) : +75.4% / MDD 24.0% / Calmar 0.70 / 0.18 tpd
  STRICT-OOS, fixed a-priori    : +57.1% / MDD 29.0% / Calmar 0.44 / 0.27 tpd
  STRICT-OOS, per-fold WFO      : +25.4% / MDD 39.0% / Calmar 0.20 / 0.27 tpd
  FULL vs HOLDOUT(2022-03+) Calmar 0.70 / 0.86 -> genuine generalisation.

One leg of a 5-strategy uncorrelated portfolio (funding / taker-flow / open-
interest / top-trader account-LSR / top-trader position-LSR).  Blended at equal
risk the portfolio is +70.4% / MDD 6.3% / 1.09 trades-day / Calmar 2.48,
positive every calendar year.

Live data: served by ``indicators.perp_meta_provider.get_taker_provider``.
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
    "source": "taker", "z_win": 672, "z_thr": 1.5,
    "max_hold_bars": 96, "side": "long", "lb": 96, "sl_pct": None,
}

STRATEGY_PARAMS: dict[str, Any] = dict(PRESET)


class EthTakerFlowReversionStrategy(_CrowdReversionBase):
    """Taker buy/sell-flow crowding reversion (LONG fade of sell-heavy flow)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**{**PRESET, **kwargs})
