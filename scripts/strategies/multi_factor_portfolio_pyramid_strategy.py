"""MFP Pyramid: adds same-direction pyramiding on top of the base MFP.

The base ``MultiFactorPortfolioStrategy`` only ever holds a single net
position: it ``enter_long``/``enter_short`` from flat, ``flip_position`` on a
direction change, and ``close_position`` when the leg majority goes flat. It
never *adds* to a winning position, so the runner trade-setting
``max_pyramid_entries`` has no effect with the stock strategy.

This variant keeps every base entry/flip/flat decision unchanged and layers a
pyramiding rule on top:

  * After the base has committed a net direction, if that same direction is
    still held on a later bar AND the leg-majority conviction has *strengthened*
    since entry (or the last add), the strategy issues an additional
    same-direction entry via ``ctx.add_to_long`` / ``ctx.add_to_short``.
  * Adds are gated by three knobs so we only scale into confirmed winners
    (trend-following behaviour) rather than averaging down into losers.

Trigger gates (all must pass):
  1. Conviction step   - the number of legs agreeing with the held side must be
     at least ``pyramid_conviction_step`` higher than it was at entry / last add.
  2. Profit gate       - unrealized PnL must be >= ``pyramid_min_profit_pct`` of
     the deployed capital (skip if 0 → no profit requirement).
  3. Cooldown          - at least ``pyramid_cooldown_bars`` base (15m) bars must
     have elapsed since the previous add.

The HARD cap on the number of adds per position is the runner trade-setting
``max_pyramid_entries`` (0 = pyramiding disabled), enforced inside
``ctx.add_to_long`` / ``ctx.add_to_short``. This strategy mirrors that cap to
avoid emitting no-op add attempts, and resets all pyramid tracking whenever the
position goes flat or flips.

Note: ``max_pyramid_entries`` lives in the trade/risk settings, NOT in the
strategy params below. Set it > 0 on the job for this strategy to do anything.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Alias the parent so the shared strategy auto-loader (which picks the first
# attribute whose name ends with ``Strategy``) does not select the imported
# base class instead of this subclass.
from multi_factor_portfolio_strategy import (  # noqa: E402
    MultiFactorPortfolioStrategy as _MFPBase,
)

from strategy.context import StrategyContext  # noqa: E402

DEFAULTS: dict[str, Any] = {
    # How many MORE legs must agree with the held side (vs. entry / last add)
    # before another pyramid entry is allowed. 0 disables the conviction gate.
    "pyramid_conviction_step": 2,
    # Minimum unrealized PnL (as a fraction of deployed capital) required before
    # adding. 0.005 = +0.5%. Set 0 to add regardless of current PnL.
    "pyramid_min_profit_pct": 0.005,
    # Minimum number of base (15m) bars between consecutive adds.
    "pyramid_cooldown_bars": 4,
    # Explicit per-add sizing fraction. 0 (default) delegates sizing to the
    # runner trade-settings (max_position / max_order), same as base entries.
    "pyramid_entry_pct": 0.0,
}


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "pyramid_conviction_step",
        "type": "int",
        "label": "Extra agreeing legs required before each add (0 = off)",
    },
    {
        "name": "pyramid_min_profit_pct",
        "type": "float",
        "label": "Min unrealized profit before adding (fraction of deployed capital)",
    },
    {
        "name": "pyramid_cooldown_bars",
        "type": "int",
        "label": "Min base (15m) bars between adds",
    },
    {
        "name": "pyramid_entry_pct",
        "type": "float",
        "label": "Per-add sizing fraction (0 = use runner default sizing)",
    },
]


class MultiFactorPortfolioPyramidStrategy(_MFPBase):
    """Base MFP + same-direction pyramiding into strengthening conviction."""

    def __init__(self, **kwargs: Any) -> None:
        my_p = {k: kwargs.pop(k, v) for k, v in DEFAULTS.items()}
        super().__init__(**kwargs)
        self.pyramid_conviction_step = int(my_p["pyramid_conviction_step"])
        self.pyramid_min_profit_pct = float(my_p["pyramid_min_profit_pct"])
        self.pyramid_cooldown_bars = int(my_p["pyramid_cooldown_bars"])
        self.pyramid_entry_pct = float(my_p["pyramid_entry_pct"])
        self.params = {**self.params, **my_p}

        # Per-position pyramid tracking. Reset on flat / flip.
        self._pyr_side: int = 0          # side the tracker currently follows
        self._pyr_ref_conv: int = 0      # agreeing-leg count at entry / last add
        self._pyr_bars_held: int = 0     # bars since entry (entry bar = 0)
        self._pyr_bars_at_last_add: int = 0
        self._pyr_adds: int = 0          # adds performed this position (informational)

    # ------------------------------------------------------------------ utils
    def _reset_pyramid(self) -> None:
        self._pyr_side = 0
        self._pyr_ref_conv = 0
        self._pyr_bars_held = 0
        self._pyr_bars_at_last_add = 0
        self._pyr_adds = 0

    @staticmethod
    def _safe_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _ctx_side(self, ctx: Any) -> int:
        size = self._safe_float(getattr(ctx, "position_size", 0.0), 0.0)
        if abs(size) < 1e-12:
            return 0
        return 1 if size > 0.0 else -1

    def _max_pyramid_entries(self, ctx: Any) -> int:
        """Read the runner-enforced pyramid cap from the risk config.

        Returns -1 when the cap cannot be read, in which case we still attempt
        the add and let ``ctx.add_to_*`` enforce the real limit.
        """
        rm = getattr(ctx, "risk_manager", None)
        cfg = getattr(rm, "config", None) if rm is not None else None
        if cfg is None:
            return -1
        try:
            return int(getattr(cfg, "max_pyramid_entries", 0))
        except (TypeError, ValueError):
            return -1

    def _deployed_capital(self, ctx: Any, size: float, entry_price: float) -> float:
        entry_balance = self._safe_float(getattr(ctx, "position_entry_balance", 0.0), 0.0)
        if entry_balance > 0.0:
            return entry_balance
        pos = getattr(ctx, "position", None)
        if pos is not None:
            eb = self._safe_float(getattr(pos, "entry_balance", 0.0), 0.0)
            if eb > 0.0:
                return eb
        leverage = self._safe_float(getattr(ctx, "leverage", 0.0), 0.0)
        notional = abs(size) * max(entry_price, 0.0)
        if leverage > 0.0 and notional > 0.0:
            return notional / leverage
        return notional

    def _pnl_ratio(self, ctx: Any) -> float:
        size = self._safe_float(getattr(ctx, "position_size", 0.0), 0.0)
        if abs(size) < 1e-12:
            return 0.0
        entry_price = self._safe_float(getattr(ctx, "position_entry_price", 0.0), 0.0)
        if entry_price <= 0.0:
            return 0.0
        deployed = self._deployed_capital(ctx, size, entry_price)
        if deployed <= 0.0:
            return 0.0
        unrealized = self._safe_float(getattr(ctx, "unrealized_pnl", 0.0), 0.0)
        return unrealized / deployed

    # ------------------------------------------------------------- reconcile
    def _reconcile(self, ctx: StrategyContext, target: int, long_count: int,
                   short_count: int, ts: int) -> None:
        side_before = self._ctx_side(ctx)

        # Let the base handle all entry / flip / flat decisions first.
        super()._reconcile(ctx, target, long_count, short_count, ts)

        side_after = self._ctx_side(ctx)

        # Flat after reconcile → nothing to pyramid into.
        if side_after == 0:
            self._reset_pyramid()
            return

        # Fresh entry or flip on THIS bar (flat→dir or dir→opposite): (re)baseline
        # the pyramid tracker and never add on the same bar as the initial entry.
        if side_after != self._pyr_side or side_before != side_after:
            self._pyr_side = side_after
            self._pyr_ref_conv = long_count if side_after > 0 else short_count
            self._pyr_bars_held = 0
            self._pyr_bars_at_last_add = 0
            self._pyr_adds = 0
            return

        # Same direction held across this bar → consider a pyramid add.
        self._pyr_bars_held += 1
        self._maybe_pyramid(ctx, side_after, long_count, short_count, ts)

    def _maybe_pyramid(self, ctx: Any, side: int, long_count: int,
                       short_count: int, ts: int) -> None:
        add_fn_name = "add_to_long" if side > 0 else "add_to_short"
        add_fn = getattr(ctx, add_fn_name, None)
        if not callable(add_fn):
            return

        # Cap check (also enforced by ctx; checked here to avoid no-op attempts).
        # max_entries == 0 disables pyramiding; -1 means "unknown" (let ctx decide).
        max_entries = self._max_pyramid_entries(ctx)
        cur_adds = int(self._safe_float(getattr(ctx, "pyramid_count", 0), 0.0))
        if max_entries == 0 or (max_entries > 0 and cur_adds >= max_entries):
            return

        # Conviction gate: same-side leg count must have strengthened.
        conv = long_count if side > 0 else short_count
        conv_gain = conv - self._pyr_ref_conv
        if self.pyramid_conviction_step > 0 and conv_gain < self.pyramid_conviction_step:
            return

        # Cooldown gate.
        if self.pyramid_cooldown_bars > 0 and (
            self._pyr_bars_held - self._pyr_bars_at_last_add
        ) < self.pyramid_cooldown_bars:
            return

        # Profit gate (skip if disabled).
        if self.pyramid_min_profit_pct > 0.0:
            ratio = self._pnl_ratio(ctx)
            if ratio < self.pyramid_min_profit_pct:
                return

        entry_pct = self.pyramid_entry_pct if self.pyramid_entry_pct > 0.0 else None
        label = "long" if side > 0 else "short"
        reason = (
            f"MFP Pyramid: add {label} #{self._pyr_adds + 1} "
            f"(conv {conv} >= ref {self._pyr_ref_conv}+{self.pyramid_conviction_step})"
        )

        before_adds = cur_adds
        try:
            add_fn(reason=reason, entry_pct=entry_pct)
        except Exception:  # noqa: BLE001
            return
        after_adds = int(
            self._safe_float(getattr(ctx, "pyramid_count", before_adds), float(before_adds))
        )

        # Only advance our tracker if the context actually accepted the add.
        if after_adds > before_adds:
            self._pyr_adds += 1
            self._pyr_ref_conv = conv            # require further strengthening next time
            self._pyr_bars_at_last_add = self._pyr_bars_held
            self._emit_event(ctx, "MFP_PYRAMID_ADD", {
                "ts": ts,
                "side": int(side),
                "pyramid_count": int(after_adds),
                "max_pyramid_entries": int(max_entries),
                "conviction": int(conv),
                "ref_conviction": int(self._pyr_ref_conv),
                "long_legs": int(long_count),
                "short_legs": int(short_count),
                "bars_held": int(self._pyr_bars_held),
                "pnl_ratio": float(self._pnl_ratio(ctx)),
            })

    # -------------------------------------------- snapshot persistence (live)
    def _build_snapshot(self) -> dict[str, Any]:
        snap = super()._build_snapshot()
        snap["pyramid"] = {
            "side": int(self._pyr_side),
            "ref_conv": int(self._pyr_ref_conv),
            "bars_held": int(self._pyr_bars_held),
            "bars_at_last_add": int(self._pyr_bars_at_last_add),
            "adds": int(self._pyr_adds),
        }
        return snap

    def _restore_from_snapshot(self, snap: dict[str, Any]) -> bool:
        ok = super()._restore_from_snapshot(snap)
        if not ok:
            return False
        st = snap.get("pyramid")
        if not isinstance(st, dict):
            self._reset_pyramid()
            return True
        side = int(self._safe_float(st.get("side", 0), 0.0))
        self._pyr_side = 1 if side > 0 else (-1 if side < 0 else 0)
        self._pyr_ref_conv = int(self._safe_float(st.get("ref_conv", 0), 0.0))
        self._pyr_bars_held = int(self._safe_float(st.get("bars_held", 0), 0.0))
        self._pyr_bars_at_last_add = int(self._safe_float(st.get("bars_at_last_add", 0), 0.0))
        self._pyr_adds = int(self._safe_float(st.get("adds", 0), 0.0))
        return True
