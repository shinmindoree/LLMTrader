"""ETHUSDT 15m — TOP-TRADER POSITION long/short-ratio reversion (leg 5 of 5).

Mechanism (orthogonal data source: Binance TOP-TRADER POSITION long/short ratio,
``sum_toptrader_long_short_ratio``):
  Position-weighted top-trader exposure -- a DIFFERENT cohort metric from the
  account-count ratio (leg 4): it weights by position size rather than counting
  accounts, and the two are statistically uncorrelated (monthly-return corr
  ~0.16).  When top-trader positioning is abnormally long (z>thr) -> fade SHORT;
  abnormally short (z<-thr) -> fade LONG.  Deployed BOTH sides with a SHORT 12h
  hold (this signal mean-reverts faster than the account-count ratio).

Config (a-priori, never re-tuned): z_win=1344, z_thr=2.0, max_hold=48 (12h),
  side=both, lb=96.  Pure time exit, no TP/SL.

Validated single-position metrics (ETHUSDT 15m, 10bp round-trip cost):
  IN-SAMPLE (full-window tuned) : +52.7% / MDD 18.1% / Calmar 0.65 / 0.24 tpd
  STRICT-OOS, fixed a-priori    : +52.7% / MDD 18.1% / Calmar 0.65 / 0.24 tpd
  STRICT-OOS, per-fold WFO      : +35.1% / MDD 28.1% / Calmar 0.37 / 0.34 tpd
  FULL vs HOLDOUT(2022-03+) Calmar 0.65 / 0.75 -> robust generalisation.

One leg of a 5-strategy uncorrelated portfolio (funding / taker-flow / open-
interest / top-trader account-LSR / top-trader position-LSR).  Blended at equal
risk the portfolio is +70.4% / MDD 6.3% / 1.09 trades-day / Calmar 2.48,
positive every calendar year.

Live data: the top-trader ratios live in ``ETHUSDT_lsr_5m.parquet`` but are NOT
yet exposed by ``get_lsr_provider`` (which serves the global
``count_long_short_ratio``).  Backtest reads the parquet directly; live
deployment requires extending the LSR provider / Redis feed with
``sum_toptrader_long_short_ratio``.
"""
from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path
from typing import Any


def _import_crowd_reversion_base():
    """Import the shared base resiliently.

    A plain sibling import works when this file runs from ``scripts/strategies/``.
    The AlphaWeaver quick-backtest, however, materialises strategy code to a temp
    file where the sibling is absent; in that case we locate
    ``eth_crowd_reversion_base.py`` by searching upward from this file and from
    the current working directory (the repo root when the API server runs there).
    """
    try:
        import eth_crowd_reversion_base as _b
        return _b
    except Exception:  # noqa: BLE001
        pass
    seen: set[Path] = set()
    for _start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for _d in (_start, *_start.parents):
            for _c in (_d / "eth_crowd_reversion_base.py",
                       _d / "scripts" / "strategies" / "eth_crowd_reversion_base.py"):
                _rc = _c.resolve()
                if _rc in seen:
                    continue
                seen.add(_rc)
                if _rc.is_file():
                    if str(_rc.parent) not in sys.path:
                        sys.path.insert(0, str(_rc.parent))
                    _spec = _ilu.spec_from_file_location("eth_crowd_reversion_base", _rc)
                    _m = _ilu.module_from_spec(_spec)
                    sys.modules["eth_crowd_reversion_base"] = _m
                    _spec.loader.exec_module(_m)
                    return _m
    raise ModuleNotFoundError(
        "eth_crowd_reversion_base.py not found next to this strategy or under "
        "<cwd>/scripts/strategies/; keep the base module alongside the leg files."
    )


_base = _import_crowd_reversion_base()
_CrowdReversionBase = _base.CrowdReversionStrategy
STRATEGY_PARAM_SCHEMA = _base.STRATEGY_PARAM_SCHEMA  # noqa: F401

PRESET: dict[str, Any] = {
    "source": "lsr_top_pos", "z_win": 1344, "z_thr": 2.0,
    "max_hold_bars": 48, "side": "both", "lb": 96, "sl_pct": None,
}

STRATEGY_PARAMS: dict[str, Any] = dict(PRESET)


class EthLsrPositionReversionStrategy(_CrowdReversionBase):
    """Top-trader position long/short-ratio crowding reversion (both sides)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**{**PRESET, **kwargs})
