"""Per-leg trailing-stop wrapper around MultiFactorPortfolioDefensiveStrategy.

Motivation
----------
Each leg in MFP already has a TP and SL set in its config. The TP is a
fixed % above (long) or below (short) entry — meaning a leg that runs
+1.7 % on a TP of 2.0 % gives all of it back if the bar then retraces.
Because each leg's exit feeds the portfolio's net-direction majority,
those give-backs ripple into the portfolio PnL the user observed:
"unrealized was + for a long time, then turned -" before the leg
majority changed.

This variant lets a leg "lock in" some of its in-flight profit via a
trailing stop:

* ``trail_activation_pct``: trailing engages once a leg's favourable
  excursion (from entry) reaches this fraction. Below this, only the
  parent's regular TP / SL / time exits apply.
* ``trail_pct``: once activated, the trailing stop sits this fraction
  below the running peak (long) or above the running trough (short).
  The leg exits the next bar that prints a high/low through the
  trailing level.

Exit-priority (per bar, mirroring parent leg-exit logic):
  1. Hard SL  (gap-style worst case fill, has priority)
  2. Trailing stop (only when activated)
  3. Hard TP
  4. Time exit (max_hold_bars)

Snapshot persistence
--------------------
Peak / trough levels are persisted alongside the leg state so a runner
restart resumes trailing from the correct level instead of resetting
the peak to ``entry_price`` and effectively widening the stop. Without
this, a restart immediately after a big in-flight gain would reset
trailing protection.

Caveats
-------
* Detection uses the just-closed bar's high/low (same cadence as
  ``new_bar_only``). Intra-bar wicks between bar closes are invisible.
* The trailing exit fills at the trailing-stop level in backtest, not
  at the bar close (matches how MFP's TP/SL are modelled — see
  ``_LegState`` exit branch).
* Backtest first: tightening leg exits reduces the average leg hold,
  which lowers the net-direction "stickiness" and may change trade
  counts substantially.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# NOTE: alias the parent so its class name does not end with ``Strategy``.
# The shared strategy auto-loader picks the first attribute whose name
# ends with ``Strategy``; without the alias it would pick the imported
# parent class instead of this subclass.
from multi_factor_portfolio_defensive_strategy import (  # noqa: E402
    MultiFactorPortfolioDefensiveStrategy as _MFPDefensiveBase,
)
from multi_factor_portfolio_strategy import _LegState  # noqa: E402


DEFAULTS: dict[str, Any] = {
    "trail_activation_pct": 0.012,
    "trail_pct": 0.006,
}


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "trail_activation_pct", "type": "float",
        "label": "Trailing activation threshold (fraction of entry)",
    },
    {
        "name": "trail_pct", "type": "float",
        "label": "Trailing distance from peak (fraction)",
    },
]


class MultiFactorPortfolioTrailingStrategy(_MFPDefensiveBase):
    """MFPD + per-leg trailing stop on top of the existing TP/SL exits."""

    def __init__(self, **kwargs: Any) -> None:
        my_p = {k: kwargs.pop(k, v) for k, v in DEFAULTS.items()}
        super().__init__(**kwargs)
        self.trail_activation_pct = float(my_p["trail_activation_pct"])
        self.trail_pct = float(my_p["trail_pct"])
        self.params = {**self.params, **my_p}
        # Per-leg peak (long) / trough (short) tracking. Keyed by
        # ``id(leg)`` because ``_LegState`` uses ``__slots__`` and can't
        # accept ad-hoc attributes. ``id(leg)`` stays stable for the
        # leg's lifetime (we never rebuild the list mid-run).
        self._leg_peaks: dict[int, float] = {}

    # ---- exit/entry logic with trailing inserted -------------------------
    # NOTE: replicates parent ``_process_leg`` rather than super()-calling
    # because the trailing exit slots between SL and TP and we need the
    # bar's high/low to update the running peak/trough either way.
    def _process_leg(self, leg: _LegState, tf_idx: int) -> None:
        # 1) Exit logic against the just-closed bar (idx = tf_idx).
        if (
            leg.side != 0
            and leg.entry_price is not None
            and leg.entry_tf_idx is not None
        ):
            o = float(leg.tf_open[tf_idx])
            h = float(leg.tf_high[tf_idx])
            lo = float(leg.tf_low[tf_idx])
            ep = leg.entry_price
            key = id(leg)
            # Initialise peak/trough to entry on the first bar after entry.
            peak = self._leg_peaks.get(key, ep)
            activation = float(self.trail_activation_pct)
            trail = float(self.trail_pct)

            if leg.side > 0:
                # Update running peak with this bar's high.
                new_peak = peak if h <= peak else h
                self._leg_peaks[key] = new_peak

                tp_level = ep * (1.0 + leg.tp_pct)
                sl_level = ep * (1.0 - leg.sl_pct)
                activation_level = (
                    ep * (1.0 + activation) if activation > 0.0 else ep
                )
                activated = (
                    activation > 0.0
                    and trail > 0.0
                    and new_peak >= activation_level
                )
                trail_stop = new_peak * (1.0 - trail) if activated else None

                # SL has priority over every other exit (gap-style fill).
                if o <= sl_level:
                    self._exit_leg(leg, key)
                    return
                if lo <= sl_level:
                    self._exit_leg(leg, key)
                    return
                # Trailing stop (only after activation).
                if activated and trail_stop is not None and lo <= trail_stop:
                    self._exit_leg(leg, key)
                    return
                # Hard TP.
                if h >= tp_level:
                    self._exit_leg(leg, key)
                    return
            else:  # short
                # Update running trough with this bar's low.
                new_peak = peak if lo >= peak else lo
                self._leg_peaks[key] = new_peak

                tp_level = ep * (1.0 - leg.tp_pct)
                sl_level = ep * (1.0 + leg.sl_pct)
                activation_level = (
                    ep * (1.0 - activation) if activation > 0.0 else ep
                )
                activated = (
                    activation > 0.0
                    and trail > 0.0
                    and new_peak <= activation_level
                )
                trail_stop = new_peak * (1.0 + trail) if activated else None

                if o >= sl_level:
                    self._exit_leg(leg, key)
                    return
                if h >= sl_level:
                    self._exit_leg(leg, key)
                    return
                if activated and trail_stop is not None and h >= trail_stop:
                    self._exit_leg(leg, key)
                    return
                if lo <= tp_level:
                    self._exit_leg(leg, key)
                    return

            # Time exit.
            if (tf_idx - leg.entry_tf_idx) >= leg.max_hold_bars:
                self._exit_leg(leg, key)
                return

        # 2) Entry on this closed bar's signal (edge-triggered).
        if leg.side == 0 and tf_idx < len(leg.long_sig):
            if bool(leg.long_sig[tf_idx]):
                leg.side = 1
                leg.entry_price = float(leg.tf_close[tf_idx])
                leg.entry_tf_idx = tf_idx
                leg.entry_tf_ts = int(leg.tf_ts[tf_idx])
                # Seed peak at the entry price.
                self._leg_peaks[id(leg)] = float(leg.entry_price)
            elif bool(leg.short_sig[tf_idx]):
                leg.side = -1
                leg.entry_price = float(leg.tf_close[tf_idx])
                leg.entry_tf_idx = tf_idx
                leg.entry_tf_ts = int(leg.tf_ts[tf_idx])
                self._leg_peaks[id(leg)] = float(leg.entry_price)

    @staticmethod
    def _reset_leg(leg: _LegState) -> None:
        leg.side = 0
        leg.entry_price = None
        leg.entry_tf_idx = None
        leg.entry_tf_ts = None

    def _exit_leg(self, leg: _LegState, key: int) -> None:
        self._reset_leg(leg)
        self._leg_peaks.pop(key, None)

    # ---- snapshot persistence: piggyback per-leg peaks -------------------
    def _build_snapshot(self) -> dict[str, Any]:
        snap = super()._build_snapshot()
        # Mirror the snapshot's leg ordering so restore can map peaks
        # back without relying on object identity (id(leg) changes across
        # process restarts).
        snap_legs = snap.get("legs") or []
        peaks_by_index: list[float | None] = []
        for leg in self._legs:
            p = self._leg_peaks.get(id(leg))
            peaks_by_index.append(float(p) if p is not None else None)
        # Trim/pad to match leg list (defensive — should already match).
        if len(peaks_by_index) != len(snap_legs):
            peaks_by_index = peaks_by_index[: len(snap_legs)]
        snap["leg_peaks"] = peaks_by_index
        return snap

    def _restore_from_snapshot(self, snap: dict[str, Any]) -> bool:
        ok = super()._restore_from_snapshot(snap)
        if not ok:
            return False
        # Rebuild the peaks dict from the snapshot's parallel list.
        self._leg_peaks.clear()
        peaks = snap.get("leg_peaks") or []
        if isinstance(peaks, list):
            for leg, p in zip(self._legs, peaks):
                if p is None:
                    continue
                try:
                    self._leg_peaks[id(leg)] = float(p)
                except (TypeError, ValueError):
                    continue
        # For any active leg without a peak (older snapshot or partial
        # restore) seed at entry_price so trailing starts from a known
        # baseline rather than nothing.
        for leg in self._legs:
            if leg.side != 0 and leg.entry_price is not None:
                self._leg_peaks.setdefault(id(leg), float(leg.entry_price))
        return True
