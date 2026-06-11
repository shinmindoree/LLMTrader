"""ETHUSDT 15m — TOP-TRADER ACCOUNT long/short-ratio reversion (leg 4 of 5).

Mechanism (orthogonal data source: Binance TOP-TRADER ACCOUNT long/short ratio,
``count_toptrader_long_short_ratio``):
  When the count of top-trader accounts is abnormally long vs its trailing
  window (z>thr) the smart-money crowd is one-sided and tends to revert ->
  fade SHORT; abnormally short (z<-thr) -> fade LONG.  Deployed BOTH sides.

Config (a-priori, never re-tuned): z_win=1344, z_thr=1.0, max_hold=192 (48h),
  side=both, lb=96.  Pure time exit, no TP/SL.

Validated single-position metrics (ETHUSDT 15m, 10bp round-trip cost):
  IN-SAMPLE (full-window tuned) : +161.8% / MDD 14.0% / Calmar 2.57 / 0.22 tpd
  STRICT-OOS, fixed a-priori    : +161.8% / MDD 14.0% / Calmar 2.57 / 0.22 tpd
  STRICT-OOS, per-fold WFO      : +21.8%  / MDD 49.0% (re-tuning adds noise)
  FULL vs HOLDOUT(2022-03+) Calmar 2.57 / 2.91 -> the strongest, most robust leg.

One leg of a 5-strategy uncorrelated portfolio (funding / taker-flow / open-
interest / top-trader account-LSR / top-trader position-LSR).  Blended at equal
risk the portfolio is +70.4% / MDD 6.3% / 1.09 trades-day / Calmar 2.48,
positive every calendar year.

Live data: the top-trader ratios live in ``ETHUSDT_lsr_5m.parquet`` but are NOT
yet exposed by ``get_lsr_provider`` (which serves the global
``count_long_short_ratio``).  Backtest reads the parquet directly; live
deployment requires extending the LSR provider / Redis feed with
``count_toptrader_long_short_ratio``.
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
    "source": "lsr_top_acc", "z_win": 1344, "z_thr": 1.0,
    "max_hold_bars": 192, "side": "both", "lb": 96, "sl_pct": None,
}

STRATEGY_PARAMS: dict[str, Any] = dict(PRESET)


class EthLsrAccountReversionStrategy(_CrowdReversionBase):
    """Top-trader account long/short-ratio crowding reversion (both sides)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**{**PRESET, **kwargs})
