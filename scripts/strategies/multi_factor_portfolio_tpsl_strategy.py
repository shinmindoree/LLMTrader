"""Portfolio-level TP/SL wrapper around MultiFactorPortfolioDefensiveStrategy.

Motivation
----------
Production MFP (and its defensive vol-gated variant) closes only when the
leg-majority direction flips or goes flat. While the position is held, an
intra-cycle favourable price move (+ unrealized PnL) is never realised:
when the majority eventually turns, price has often retraced past entry,
so the realised PnL is negative.

This variant adds a **portfolio-level** TP and SL on top of the leg
majority logic:

* ``portfolio_tp_pct``: close & lock profit when unrealized rises beyond
  this fraction of entry (e.g. 0.020 = +2%).
* ``portfolio_sl_pct``: close & cut loss when unrealized drops beyond
  this fraction of entry (e.g. 0.015 = -1.5%).

After a TP/SL fires, re-entry in the **same** direction is blocked until
the leg majority changes (``reentry_block_until_target_change``). Without
this guard the strategy would immediately re-enter at the next bar's
close, defeating the purpose of locking in the move.

Important caveats (read before relying on this in live)
-------------------------------------------------------
* The detection uses the just-closed 15m bar's high/low (same cadence as
  ``new_bar_only``). Intra-bar wick movements *between* bar closes are
  invisible. This matches the leg-level TP/SL semantics already in MFP.
* This changes the trade distribution materially. Backtest first — the
  baseline strategy was designed to ride 2-5 % moves; a tight TP cuts
  winners short and over many runs may net negative even though it
  fixes the user-reported "+PnL → -PnL" anti-pattern.
* SL has priority over TP in the same bar (gap-style worst-case fill,
  mirroring ``_LegState`` exit-priority).

Snapshot persistence
--------------------
The portfolio entry price, side, and the post-TP/SL block flag are
piggybacked onto the parent's Redis snapshot via
``_build_snapshot`` / ``_restore_from_snapshot`` overrides so a runner
restart resumes tracking the active TP/SL window. If the snapshot is
missing or stale and the parent reconstructs leg state via warmup
replay, the first ``on_bar`` falls back to ``ctx.position_entry_price``
to recover the portfolio entry without losing the in-flight TP/SL.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# NOTE: alias the parent so its class name does not end with ``Strategy``.
# The shared strategy auto-loader (`scripts/run_backtest.py`,
# `src/runner/strategy_loader.py`, ...) picks the first attribute whose
# name ends with ``Strategy`` -- without the alias it would pick the
# imported parent class instead of this subclass.
from multi_factor_portfolio_defensive_strategy import (  # noqa: E402
    MultiFactorPortfolioDefensiveStrategy as _MFPDefensiveBase,
)
from multi_factor_portfolio_strategy import _bar_ts  # noqa: E402
from strategy.context import StrategyContext  # noqa: E402


DEFAULTS: dict[str, Any] = {
    "portfolio_tp_pct": 0.020,
    "portfolio_sl_pct": 0.015,
    "reentry_block_until_target_change": True,
}


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "portfolio_tp_pct", "type": "float",
        "label": "Portfolio take-profit (fraction of entry, e.g. 0.020 = +2%)",
    },
    {
        "name": "portfolio_sl_pct", "type": "float",
        "label": "Portfolio stop-loss (fraction of entry, e.g. 0.015 = -1.5%)",
    },
    {
        "name": "reentry_block_until_target_change", "type": "bool",
        "label": "Block re-entry in same direction until leg majority changes",
    },
]


class MultiFactorPortfolioTpSlStrategy(_MFPDefensiveBase):
    """MFPD + portfolio-level take-profit / stop-loss."""

    def __init__(self, **kwargs: Any) -> None:
        my_p = {k: kwargs.pop(k, v) for k, v in DEFAULTS.items()}
        super().__init__(**kwargs)
        self.portfolio_tp_pct = float(my_p["portfolio_tp_pct"])
        self.portfolio_sl_pct = float(my_p["portfolio_sl_pct"])
        self.reentry_block_until_target_change = bool(
            my_p["reentry_block_until_target_change"]
        )
        self.params = {**self.params, **my_p}

        # Portfolio-level position tracking. Set when _committed_side
        # transitions 0 -> non-zero in ``on_bar`` after super; cleared on
        # any exit (flip, flat, TP, SL).
        self._pf_entry_price: float | None = None
        self._pf_entry_side: int = 0
        # Direction whose entry is currently blocked due to a recent
        # portfolio TP/SL. 0 = no block; +1 = blocked from re-entering
        # long; -1 = blocked from re-entering short. Cleared when the
        # leg-majority ``target`` differs from the blocked side.
        self._pf_blocked_side: int = 0

    # ---- bar processing ----------------------------------------------------
    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # Mirror parent's gating so we don't act on intra-bar ticks when
        # ``new_bar_only`` is set.
        if self.new_bar_only and not bool(bar.get("is_new_bar", True)):
            return
        ts = _bar_ts(bar)
        if ts <= 0 or ts == self._last_bar_ts:
            return

        # If we restored from a snapshot mid-trade (or warmup replay
        # rebuilt leg state) but ``_pf_entry_price`` was never set,
        # recover it from the live runner's position state. This guards
        # against the case where snapshot restore lost our portfolio-
        # level tracking but the actual exchange position is intact.
        if (
            self._committed_side != 0
            and self._pf_entry_price is None
        ):
            try:
                ep = float(getattr(ctx, "position_entry_price", 0.0) or 0.0)
            except (TypeError, ValueError):
                ep = 0.0
            if ep > 0.0:
                self._pf_entry_price = ep
                self._pf_entry_side = int(self._committed_side)

        # ---- 1) Portfolio TP/SL check on this newly-arrived bar -----------
        # Done BEFORE super().on_bar so the legs see the post-exit state
        # and ``_reconcile`` will not immediately re-open in the same
        # direction (the re-entry block flag is what enforces that).
        if (
            self._committed_side != 0
            and self._pf_entry_price is not None
            and self._pf_entry_price > 0.0
        ):
            try:
                h = float(bar.get("high"))
                lo = float(bar.get("low"))
            except (TypeError, ValueError):
                h = lo = float("nan")
            ep = self._pf_entry_price
            side = self._pf_entry_side
            tp_pct = float(self.portfolio_tp_pct)
            sl_pct = float(self.portfolio_sl_pct)

            exit_kind: str | None = None
            exit_level: float | None = None
            if side > 0:
                tp_level = ep * (1.0 + tp_pct) if tp_pct > 0.0 else None
                sl_level = ep * (1.0 - sl_pct) if sl_pct > 0.0 else None
                # SL has priority (gap-down worst-case).
                if sl_level is not None and lo == lo and lo <= sl_level:
                    exit_kind = "portfolio_sl"
                    exit_level = sl_level
                elif tp_level is not None and h == h and h >= tp_level:
                    exit_kind = "portfolio_tp"
                    exit_level = tp_level
            elif side < 0:
                tp_level = ep * (1.0 - tp_pct) if tp_pct > 0.0 else None
                sl_level = ep * (1.0 + sl_pct) if sl_pct > 0.0 else None
                # SL has priority (gap-up worst-case).
                if sl_level is not None and h == h and h >= sl_level:
                    exit_kind = "portfolio_sl"
                    exit_level = sl_level
                elif tp_level is not None and lo == lo and lo <= tp_level:
                    exit_kind = "portfolio_tp"
                    exit_level = tp_level

            if exit_kind is not None:
                reason = (
                    f"MFPTPSL: {exit_kind} (ep={ep:.2f} lvl={exit_level:.2f})"
                )
                try:
                    ctx.close_position(reason=reason)
                except Exception:  # noqa: BLE001
                    pass
                prev_side = int(self._committed_side)
                self._committed_side = 0
                if self.reentry_block_until_target_change:
                    self._pf_blocked_side = prev_side
                else:
                    self._pf_blocked_side = 0
                self._emit_event(ctx, "MFP_PORTFOLIO_EXIT", {
                    "ts": ts,
                    "kind": exit_kind,
                    "prev_side": prev_side,
                    "entry_price": float(ep),
                    "exit_level": float(exit_level),
                    "bar_high": float(h) if h == h else None,
                    "bar_low": float(lo) if lo == lo else None,
                    "tp_pct": float(tp_pct),
                    "sl_pct": float(sl_pct),
                    "blocked_side": int(self._pf_blocked_side),
                })
                self._pf_entry_price = None
                self._pf_entry_side = 0

        # ---- 2) Run parent's full bar pipeline ----------------------------
        prev_committed = int(self._committed_side)
        super().on_bar(ctx, bar)

        # ---- 3) Record entry price for any NEW position super opened -----
        new_committed = int(self._committed_side)
        if new_committed == 0:
            # Either super closed (flip/flat) or our TP/SL above already
            # cleared it. Make sure we drop the tracking either way.
            self._pf_entry_price = None
            self._pf_entry_side = 0
        elif new_committed != self._pf_entry_side:
            # Side changed (fresh entry or flip-and-open). Snap entry to
            # this bar's close — that's the price the engine simulates
            # the market order at for the entry.
            try:
                close_px = float(bar.get("close"))
            except (TypeError, ValueError):
                close_px = float("nan")
            if close_px == close_px and close_px > 0.0:
                self._pf_entry_price = close_px
            else:
                # Fallback to ctx if bar.close is missing/invalid.
                try:
                    fallback = float(getattr(ctx, "position_entry_price", 0.0) or 0.0)
                except (TypeError, ValueError):
                    fallback = 0.0
                self._pf_entry_price = (
                    fallback if fallback > 0.0 else None
                )
            self._pf_entry_side = new_committed

        # The bar processed something; if target moved off the blocked
        # side we already cleared block inside ``_reconcile`` below.
        _ = prev_committed  # kept for future debug events if needed

    # ---- reconcile override: enforce re-entry block -----------------------
    def _reconcile(self, ctx: StrategyContext, target: int, long_count: int,
                   short_count: int, ts: int) -> None:
        # Clear the block as soon as the leg majority shifts AWAY from the
        # blocked direction (different side or flat). This is the cheapest
        # way to ensure we re-engage on the NEXT real direction change
        # without flapping right back into the position we just exited.
        if (
            self._pf_blocked_side != 0
            and target != self._pf_blocked_side
        ):
            self._emit_event(ctx, "MFP_PORTFOLIO_BLOCK_CLEAR", {
                "ts": ts,
                "blocked_side": int(self._pf_blocked_side),
                "new_target": int(target),
            })
            self._pf_blocked_side = 0

        # If majority still says the blocked direction, do nothing —
        # specifically skip opening a NEW position. We still want any
        # flat/flip CLOSE to run if ``_committed_side`` somehow disagrees
        # with target, so let parent handle that path only when we are
        # already in a position (cur != 0).
        if (
            self._pf_blocked_side != 0
            and target == self._pf_blocked_side
            and self._committed_side == 0
        ):
            return

        super()._reconcile(ctx, target, long_count, short_count, ts)

    # ---- snapshot persistence: piggyback portfolio state -----------------
    def _build_snapshot(self) -> dict[str, Any]:
        snap = super()._build_snapshot()
        snap["pf_entry_price"] = (
            float(self._pf_entry_price)
            if self._pf_entry_price is not None
            else None
        )
        snap["pf_entry_side"] = int(self._pf_entry_side)
        snap["pf_blocked_side"] = int(self._pf_blocked_side)
        return snap

    def _restore_from_snapshot(self, snap: dict[str, Any]) -> bool:
        ok = super()._restore_from_snapshot(snap)
        if not ok:
            return False
        ep = snap.get("pf_entry_price")
        self._pf_entry_price = float(ep) if ep is not None else None
        try:
            self._pf_entry_side = int(snap.get("pf_entry_side", 0) or 0)
        except (TypeError, ValueError):
            self._pf_entry_side = 0
        try:
            self._pf_blocked_side = int(snap.get("pf_blocked_side", 0) or 0)
        except (TypeError, ValueError):
            self._pf_blocked_side = 0
        return True
