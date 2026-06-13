"""ETHUSDT 15m — UNIFIED crowd-reversion (5 legs netted into ONE account).

Why this file exists
--------------------
The validated alpha is a portfolio of FIVE mutually-uncorrelated single-position
crowd-reversion legs on orthogonal perp-meta sources (top-trader account-LSR,
top-trader position-LSR, taker flow, funding, open-interest build-up).  The
"textbook" deployment runs each leg in its own Binance sub-account so margins are
physically isolated.  But ALL FIVE LEGS TRADE THE SAME ETHUSDT CONTRACT and the
backtest pnl is linear (additive) in position, so the five weighted legs can be
**netted into a single position on a single account** with NO loss of edge:

    N(t) = sum_leg  w_leg * pos_leg(t)            (pos_leg in {-1, 0, +1})

    GROSS pnl identity (mark-to-market, exact):
        sum_t N(t)*r(t)  ==  sum_leg w_leg * sum_t pos_leg(t)*r(t)
    COST (turnover) for the netted account is <= the 5-account sum, because
    offsetting legs reduce traded quantity:
        |N(t)-N(t-1)| = |sum w*dpos| <= sum w*|dpos|   (triangle inequality)
    => single-account performance  >=  5-account performance.

This was verified numerically in ``scripts/_alpha_lab/a5_netcheck.py``:
    max |monthly_5acct - monthly_1acct| (gross) = 6.6e-17   (== identical)
    net: 5 accounts +71.6% / MDD 5.76%  ;  1 netted account +71.9% / MDD 5.76%
    (the netted account is marginally better thanks to lower turnover cost).

So the ONLY thing five sub-accounts buy you here is operational isolation that
this same-symbol, same-direction portfolio does not actually need.  This file
collapses the whole portfolio into a single deployable strategy: one account,
one API key, one job.

Mechanism
---------
Each bar we advance all five validated leg state machines (via the shared base's
``step_target`` -- identical signal + H-bar pure-time-exit logic to the five
deployed single-leg files), combine their desired positions with the fixed
inverse-vol risk-parity weights, and rebalance the single account position to the
net target ``N(t) * unit_qty``.  ``unit_qty`` is sized off
``calc_entry_quantity(base_leverage)``; ``|N(t)| <= 1`` so ``base_leverage`` is
the fraction of (equity * leverage) deployed when all five legs align.

Deployed leg configs (a-priori FINAL_FIX, never re-tuned) and inverse-vol weights
(from the full-window risk-parity blend, ``revfinal_fix.json``):

    leg      source        z_win  z_thr   H   side   lb    weight
    LSRACC   lsr_top_acc   1344   1.0    192  both   96    0.1911
    LSRPOS   lsr_top_pos   1344   2.0     48  both   96    0.2506
    TAKER    taker          672   1.5     96  long   96    0.1986
    FUND     funding        384   1.0    192  long   96    0.1389
    OI       oi             384   1.5     96  long   96    0.2208

Live data: funding / taker / oi from the production providers; the two
top-trader LSR series from the ingestor's ``lsr_top_acc`` / ``lsr_top_pos``
Redis keys (wired in ``perp_meta_provider`` + ``perp_meta_ingestor``).
"""
from __future__ import annotations

import importlib.util as _ilu
import math
import sys
from pathlib import Path
from typing import Any


def _import_crowd_reversion_base():
    """Import the shared base resiliently (same loader the 5 leg files use)."""
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
        "<cwd>/scripts/strategies/; keep the base module alongside this file."
    )


_base = _import_crowd_reversion_base()
_CrowdReversionBase = _base.CrowdReversionStrategy
Strategy = _base.Strategy
StrategyContext = _base.StrategyContext

# leg -> (preset, inverse-vol weight).  Presets == the 5 deployed leg files /
# a5_revfinal.FINAL_FIX; weights == revfinal_fix.json (full-window risk parity).
LEGS: list[dict[str, Any]] = [
    {"name": "LSRACC", "weight": 0.1911,
     "preset": {"source": "lsr_top_acc", "z_win": 1344, "z_thr": 1.0,
                "max_hold_bars": 192, "side": "both", "lb": 96, "sl_pct": None}},
    {"name": "LSRPOS", "weight": 0.2506,
     "preset": {"source": "lsr_top_pos", "z_win": 1344, "z_thr": 2.0,
                "max_hold_bars": 48, "side": "both", "lb": 96, "sl_pct": None}},
    {"name": "TAKER", "weight": 0.1986,
     "preset": {"source": "taker", "z_win": 672, "z_thr": 1.5,
                "max_hold_bars": 96, "side": "long", "lb": 96, "sl_pct": None}},
    {"name": "FUND", "weight": 0.1389,
     "preset": {"source": "funding", "z_win": 384, "z_thr": 1.0,
                "max_hold_bars": 192, "side": "long", "lb": 96, "sl_pct": None}},
    {"name": "OI", "weight": 0.2208,
     "preset": {"source": "oi", "z_win": 384, "z_thr": 1.5,
                "max_hold_bars": 96, "side": "long", "lb": 96, "sl_pct": None}},
]

# base_leverage : fraction of (equity * system-leverage) deployed at |N|=1 (all
#   five legs aligned).  1.0 == the faithful analog of allocating 100% of capital
#   across the five sub-accounts.
# rebalance_deadband : skip dust rebalances smaller than this fraction of the
#   full-position unit quantity (avoids churn from sub-weight target wiggles).
STRATEGY_PARAMS: dict[str, Any] = {
    "base_leverage": 1.0,
    "rebalance_deadband": 0.02,
}

STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {"name": "base_leverage", "type": "float", "min": 0.05, "max": 1.0,
     "step": 0.05, "label": "Net leverage at full leg alignment (|N|=1)"},
    {"name": "rebalance_deadband", "type": "float", "min": 0.0, "max": 0.25,
     "step": 0.01, "label": "Rebalance deadband (frac of unit qty)"},
]


class EthCrowdReversionUnifiedStrategy(Strategy):
    """Five netted crowd-reversion legs as ONE single-account position."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.base_leverage = float(p["base_leverage"])
        self.rebalance_deadband = float(p["rebalance_deadband"])

        self._legs: list[tuple[str, float, Any]] = []
        for spec in LEGS:
            leg = _CrowdReversionBase(**spec["preset"])
            self._legs.append((spec["name"], float(spec["weight"]), leg))

        self.params = dict(p)
        self.indicator_config = {}
        self._last_target_qty = 0.0

    # ------------------------------------------------------------------ init
    def initialize(self, ctx: StrategyContext) -> None:
        # Each leg builds its own (live or parquet) sampler off the same ctx.
        for _name, _w, leg in self._legs:
            leg.initialize(ctx)
        self._last_target_qty = 0.0
        self._emit_event(ctx, "CROWDREV_UNIFIED_INIT", {
            "legs": [
                {"name": n, "source": leg.source, "weight": round(w, 4),
                 "z_win": leg.z_win, "z_thr": leg.z_thr,
                 "H": leg.max_hold_bars, "side": leg.side}
                for n, w, leg in self._legs
            ],
            "base_leverage": self.base_leverage,
            "rebalance_deadband": self.rebalance_deadband,
        })

    # ------------------------------------------------------------------ bar
    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if not bool(bar.get("is_new_bar", True)):
            return

        close = float(bar.get("close", bar.get("price", 0.0)) or 0.0)
        if not math.isfinite(close) or close <= 0:
            return
        ts_open = int(bar.get("bar_timestamp", bar.get("timestamp", 0)) or 0)

        # ---- advance all five validated leg state machines -------------------
        net = 0.0
        contribs: dict[str, float] = {}
        for name, weight, leg in self._legs:
            tgt = float(leg.step_target(ts_open, close))  # {-1, 0, +1}
            contribs[name] = tgt
            net += weight * tgt
        net = max(-1.0, min(1.0, net))   # |N| <= 1 by construction; clamp for safety

        # ---- map net signal -> target account quantity ----------------------
        unit_qty = 0.0
        try:
            unit_qty = float(ctx.calc_entry_quantity(entry_pct=self.base_leverage))
        except Exception:  # noqa: BLE001
            unit_qty = 0.0
        if unit_qty <= 0:
            return
        target_qty = net * unit_qty

        # ---- rebalance current position to the net target -------------------
        self._rebalance(ctx, target_qty, unit_qty, net, contribs)

    # ------------------------------------------------------------- rebalancer
    def _rebalance(self, ctx: Any, target_qty: float, unit_qty: float,
                   net: float, contribs: dict[str, float]) -> None:
        """Move the single account position to ``target_qty`` (signed).

        Backtest/live ``buy``/``sell`` cap at closing the opposite side and do
        NOT cross zero in one call, so a side flip is issued as close-then-open.
        Dust changes below the deadband are skipped to avoid churn.
        """
        try:
            cur = float(ctx.position_size)
        except Exception:  # noqa: BLE001
            cur = 0.0
        deadband = self.rebalance_deadband * unit_qty
        delta = target_qty - cur
        if abs(delta) <= deadband:
            return

        reason = (f"CrowdRevUnified N={net:+.3f} "
                  f"[{','.join(f'{k}{int(v):+d}' for k, v in contribs.items())}]")

        same_side = (cur >= 0 and target_qty >= 0) or (cur <= 0 and target_qty <= 0)
        if same_side:
            if delta > 0:
                ctx.buy(abs(delta), reason=reason)
            else:
                ctx.sell(abs(delta), reason=reason)
        else:
            # crossing zero: close current fully, then open the new side
            if cur > 0:
                ctx.sell(cur, reason="CrowdRevUnified: close long (flip)")
                if target_qty < 0:
                    ctx.sell(abs(target_qty), reason=reason)
            else:
                ctx.buy(abs(cur), reason="CrowdRevUnified: close short (flip)")
                if target_qty > 0:
                    ctx.buy(abs(target_qty), reason=reason)
        self._last_target_qty = target_qty

    # ---------------------------------------------------------------- helpers
    def _emit_event(self, ctx: Any, action: str, data: dict[str, Any]) -> None:
        fn = getattr(ctx, "log_event", None)
        if not callable(fn):
            return
        try:
            fn(action, data)
        except Exception:  # noqa: BLE001
            pass
