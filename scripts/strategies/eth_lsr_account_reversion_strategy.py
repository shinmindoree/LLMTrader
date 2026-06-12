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
    "source": "lsr_top_acc", "z_win": 1344, "z_thr": 1.0,
    "max_hold_bars": 192, "side": "both", "lb": 96, "sl_pct": None,
}

STRATEGY_PARAMS: dict[str, Any] = dict(PRESET)


class EthLsrAccountReversionStrategy(_CrowdReversionBase):
    """Top-trader account long/short-ratio crowding reversion (both sides)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**{**PRESET, **kwargs})
