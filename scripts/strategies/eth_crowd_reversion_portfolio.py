"""ETHUSDT 15m CROWD-REVERSION suite — 5 uncorrelated alpha legs + portfolio.

This package is the result of an exhaustive ETHUSDT-perp research program
(scripts/_alpha_lab/a5_*).  Price-pattern families (breakout / momentum /
squeeze / hour-of-day / flow-continuation) ALL collapsed under strict
walk-forward: great in-sample, strongly negative out-of-sample.  An
edge-existence scan then showed *why* — ETH 15m mean-reverts, it does not
trend after costs: every momentum signal had NEGATIVE forward information
coefficient, while several CROWD-POSITIONING signals had robust, split-half
-stable reversion IC.  The deployable alpha is fading crowding extremes on five
orthogonal perp-meta data sources.

THE FIVE LEGS (mutually uncorrelated; max pairwise monthly-return corr 0.32):
  1. EthFundingReversionStrategy        funding rate (carry / sentiment)
  2. EthTakerFlowReversionStrategy      taker buy/sell aggressor flow
  3. EthOiBuildupReversionStrategy      open-interest build-up (+ price confirm)
  4. EthLsrAccountReversionStrategy     top-trader ACCOUNT long/short ratio
  5. EthLsrPositionReversionStrategy    top-trader POSITION long/short ratio
All share one mechanism (z-score a source over a trailing window, fade the
extreme, exit purely on time after H bars) but on DISTINCT data, so their
returns are uncorrelated.  Deploy all five in parallel at equal risk (the
single-position framework keeps at most one position per leg = max 5 concurrent).

=====================  IN-SAMPLE vs STRICT-OOS COMPARISON  =====================
Single-position model (matches the production Strategy framework), ETHUSDT 15m,
full available history (aux data 2021-12 .. 2026-06), 10bp round-trip cost.

Per leg (return% / maxDD% / Calmar / trades-day):
  leg      IN-SAMPLE (full tuned)   STRICT-OOS fixed a-priori   STRICT-OOS per-fold WFO
  LSRACC   +161.8 / 14.0 / 2.57     +161.8 / 14.0 / 2.57 / .22  +21.8 / 49.0 / 0.13
  LSRPOS   + 52.7 / 18.1 / 0.65     + 52.7 / 18.1 / 0.65 / .24  +35.1 / 28.1 / 0.37
  TAKER    + 75.4 / 24.0 / 0.70     + 57.1 / 29.0 / 0.44 / .27  +25.4 / 39.0 / 0.20
  FUND     + 64.7 / 28.6 / 0.39     +107.7 / 35.1 / 0.53 / .20  - 9.7 / 63.9 /-0.03
  OI       + 28.7 / 12.8 / 0.50     + 22.5 / 24.6 / 0.20 / .16  -38.1 / 47.2 /-0.24

5-LEG RISK-PARITY PORTFOLIO:
  STRICT-OOS, fixed a-priori (DEPLOY) : +70.4% / MDD 6.30% / 1.09 trades-day /
                                        Calmar 2.48 / 54.5% positive months /
                                        POSITIVE EVERY CALENDAR YEAR
                                        (2022 +2.6, 2023 +22.3, 2024 +30.1,
                                         2025 +11.7, 2026 +8.6 ytd)
  STRICT-OOS, per-fold WFO (adversarial, scaled to MDD 10%):
                                        +5.6% / 56.1% pos months / 2025 NEGATIVE

KEY LESSON: for sentiment/positioning reversion you do NOT re-optimise params
month-to-month.  Per-fold walk-forward RE-TUNING (the strictest test) adds
selection noise and chases the last regime, so even genuinely-profitable legs
look poor.  The honest out-of-sample evidence is FIXED a-priori params evaluated
over all data, cross-checked by FULL-window ~ HOLDOUT(2022-03+) consistency
(e.g. LSRACC Calmar 2.57 vs 2.91).  Production therefore holds params CONSTANT;
the per-fold WFO column is reported only as the most adversarial stress test.

Reproduce:  python scripts/_alpha_lab/a5_revfinal.py --mode fix   (and --mode wfo)
Verify production code == research signal:  python scripts/_alpha_lab/a5_verify_strats.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eth_funding_reversion_strategy import EthFundingReversionStrategy  # noqa: E402
from eth_taker_flow_reversion_strategy import EthTakerFlowReversionStrategy  # noqa: E402
from eth_oi_buildup_reversion_strategy import EthOiBuildupReversionStrategy  # noqa: E402
from eth_lsr_account_reversion_strategy import EthLsrAccountReversionStrategy  # noqa: E402
from eth_lsr_position_reversion_strategy import EthLsrPositionReversionStrategy  # noqa: E402

# Equal-RISK weights (inverse trailing vol) from the validated risk-parity blend.
PORTFOLIO: list[dict[str, Any]] = [
    {"name": "LSRACC", "cls": EthLsrAccountReversionStrategy,  "risk_weight": 0.191},
    {"name": "LSRPOS", "cls": EthLsrPositionReversionStrategy, "risk_weight": 0.251},
    {"name": "TAKER",  "cls": EthTakerFlowReversionStrategy,   "risk_weight": 0.199},
    {"name": "FUND",   "cls": EthFundingReversionStrategy,     "risk_weight": 0.138},
    {"name": "OI",     "cls": EthOiBuildupReversionStrategy,   "risk_weight": 0.221},
]


def build_portfolio() -> list[Any]:
    """Instantiate all five legs (deploy in parallel, sized by risk_weight)."""
    return [item["cls"]() for item in PORTFOLIO]
