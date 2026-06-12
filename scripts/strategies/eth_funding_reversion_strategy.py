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
    "source": "funding", "z_win": 384, "z_thr": 1.0,
    "max_hold_bars": 192, "side": "long", "lb": 96, "sl_pct": None,
}

STRATEGY_PARAMS: dict[str, Any] = dict(PRESET)


class EthFundingReversionStrategy(_CrowdReversionBase):
    """Funding-rate crowding reversion (LONG fade of negative funding spikes)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**{**PRESET, **kwargs})
