"""MFP V3: V2 profit-protect exit + manual-close re-entry guard.

Manual-close guard
------------------
If the live position is manually flattened while MFP still has active legs in
the same direction, the base MFP would resync to flat and immediately re-enter
on the same leg majority. V3 treats that external flatten as intentional:

- same-side target after manual close: do not re-enter
- target becomes flat: clear the guard, no order because position is already 0
- target flips to the opposite side: clear the guard and enter the new side
- later fresh same-side signal after a flat reset: enter normally
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Alias parent name so auto-loader does not pick the imported base class first.
from multi_factor_portfolio_v2_strategy import (  # noqa: E402
    STRATEGY_PARAM_SCHEMA as _V2_PARAM_SCHEMA,
)
from multi_factor_portfolio_v2_strategy import (  # noqa: E402
    MultiFactorPortfolioV2Strategy as _MFPV2Base,
)


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = list(_V2_PARAM_SCHEMA)


class MultiFactorPortfolioV3Strategy(_MFPV2Base):
    """MFP V2 with manual flatten suppression for same-side stale targets."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._manual_close_block_side: int = 0
        self._manual_close_block_reported: bool = False

    def _clear_manual_close_guard(self) -> None:
        self._manual_close_block_side = 0
        self._manual_close_block_reported = False

    def _set_manual_close_guard(self, side: int) -> None:
        normalized = 1 if side > 0 else (-1 if side < 0 else 0)
        if normalized != self._manual_close_block_side:
            self._manual_close_block_reported = False
        self._manual_close_block_side = normalized

    def _handle_manual_close_guard(
        self,
        ctx: Any,
        target: int,
        long_count: int,
        short_count: int,
        ts: int,
    ) -> bool:
        blocked = int(self._manual_close_block_side)
        if blocked == 0:
            return False

        if target == blocked:
            if not self._manual_close_block_reported:
                self._emit_event(ctx, "MFP_MANUAL_CLOSE_REENTRY_BLOCKED", {
                    "ts": ts,
                    "blocked_side": blocked,
                    "target": int(target),
                    "long_legs": int(long_count),
                    "short_legs": int(short_count),
                })
                self._manual_close_block_reported = True
            return True

        self._emit_event(ctx, "MFP_MANUAL_CLOSE_GUARD_CLEARED", {
            "ts": ts,
            "blocked_side": blocked,
            "target": int(target),
            "long_legs": int(long_count),
            "short_legs": int(short_count),
            "reason": "target_flat" if target == 0 else "target_flip",
        })
        self._clear_manual_close_guard()
        if target == 0:
            return True
        return False

    def _close_for_profit_protect(
        self,
        ctx: Any,
        target: int,
        long_count: int,
        short_count: int,
        ts: int,
        ratio: float,
    ) -> bool:
        activation = float(self.profit_lock_activation_pct)
        exit_level = float(self.profit_lock_exit_pct)
        if activation > 0.0 and ratio >= activation:
            self._profit_lock_armed = True

        if (
            not self._profit_lock_armed
            or exit_level < 0.0
            or ratio > exit_level
        ):
            return False

        prev_side = int(self._committed_side)
        reason = (
            "MFP V3: profit-protect exit "
            f"(peak={self._profit_lock_peak_ratio * 100:.3f}%, "
            f"now={ratio * 100:.3f}%, "
            f"activation={activation * 100:.3f}%, "
            f"exit={exit_level * 100:.3f}%)"
        )
        try:
            ctx.close_position(reason=reason)
        except Exception:  # noqa: BLE001
            pass

        self._committed_side = 0
        if prev_side != 0:
            self._set_manual_close_guard(prev_side)
        self._emit_event(ctx, "MFP_PROFIT_PROTECT_EXIT", {
            "ts": ts,
            "prev_side": prev_side,
            "target": int(target),
            "long_legs": int(long_count),
            "short_legs": int(short_count),
            "pnl_ratio": ratio,
            "peak_ratio": float(self._profit_lock_peak_ratio),
            "activation_ratio": float(activation),
            "exit_ratio": float(exit_level),
            "reentry_block_side": int(self._manual_close_block_side),
        })
        self._emit_event(ctx, "MFP_FLAT", {
            "ts": ts,
            "target": 0,
            "prev_side": prev_side,
            "committed_side": 0,
            "long_legs": int(long_count),
            "short_legs": int(short_count),
            "kind": "profit_protect",
        })
        self._reset_profit_lock()
        return True

    def _reconcile(
        self,
        ctx: Any,
        target: int,
        long_count: int,
        short_count: int,
        ts: int,
    ) -> None:
        actual = self._current_side(ctx)
        cached = int(self._committed_side)

        if actual == 0:
            self._reset_profit_lock()
            if cached != 0:
                self._set_manual_close_guard(cached)
                self._committed_side = 0
                self._emit_event(ctx, "MFP_MANUAL_CLOSE_DETECTED", {
                    "ts": ts,
                    "cached_side": cached,
                    "actual_side": 0,
                    "target": int(target),
                    "long_legs": int(long_count),
                    "short_legs": int(short_count),
                    "blocked_side": int(self._manual_close_block_side),
                })

            if self._handle_manual_close_guard(ctx, target, long_count, short_count, ts):
                return

            self._reconcile_synced(ctx, target, long_count, short_count, ts)
            return

        if actual != cached:
            self._emit_event(ctx, "MFP_CTX_RESYNC", {
                "ts": ts,
                "cached_side": cached,
                "actual_side": int(actual),
            })
            self._committed_side = int(actual)
        self._clear_manual_close_guard()

        if actual != self._profit_lock_side:
            self._profit_lock_side = int(actual)
            self._profit_lock_armed = False
            self._profit_lock_peak_ratio = 0.0

        ratio = self._current_pnl_ratio(ctx)
        if ratio > self._profit_lock_peak_ratio:
            self._profit_lock_peak_ratio = ratio

        if self._close_for_profit_protect(ctx, target, long_count, short_count, ts, ratio):
            return

        self._reconcile_synced(ctx, target, long_count, short_count, ts)

    def _reconcile_synced(
        self,
        ctx: Any,
        target: int,
        long_count: int,
        short_count: int,
        ts: int,
    ) -> None:
        cur = int(self._committed_side)
        if target == cur:
            return

        if cur != 0 and target != 0 and cur != target:
            prev_label = "long" if cur > 0 else "short"
            next_label = "long" if target > 0 else "short"
            close_reason = f"MFP: net direction flip ({prev_label}->{next_label})"
            if target == 1:
                entry_reason = f"MFP: net long ({long_count}>{short_count})"
            else:
                entry_reason = f"MFP: net short ({short_count}>{long_count})"

            flip_fn = getattr(ctx, "flip_position", None)
            if callable(flip_fn):
                try:
                    flip_fn(
                        target_side=int(target),
                        close_reason=close_reason,
                        entry_reason=entry_reason,
                    )
                except Exception:  # noqa: BLE001
                    pass
            else:
                try:
                    ctx.close_position(reason=close_reason)
                except Exception:  # noqa: BLE001
                    pass
                if target == 1:
                    ctx.enter_long(reason=entry_reason)
                else:
                    ctx.enter_short(reason=entry_reason)

            self._emit_event(ctx, "MFP_FLAT", {
                "ts": ts, "target": int(target), "prev_side": int(cur),
                "committed_side": 0,
                "long_legs": long_count, "short_legs": short_count,
                "kind": "flip",
            })
            if target == 1:
                self._committed_side = 1
                self._emit_event(ctx, "MFP_ENTER_LONG", {
                    "ts": ts, "target": 1, "prev_side": int(cur),
                    "committed_side": 1,
                    "long_legs": long_count, "short_legs": short_count,
                })
            else:
                self._committed_side = -1
                self._emit_event(ctx, "MFP_ENTER_SHORT", {
                    "ts": ts, "target": -1, "prev_side": int(cur),
                    "committed_side": -1,
                    "long_legs": long_count, "short_legs": short_count,
                })
            return

        if cur != 0 and target == 0:
            close_reason = f"MFP: net flat ({long_count}={short_count})"
            try:
                ctx.close_position(reason=close_reason)
            except Exception:  # noqa: BLE001
                pass
            self._committed_side = 0
            self._clear_manual_close_guard()
            self._emit_event(ctx, "MFP_FLAT", {
                "ts": ts, "target": 0, "prev_side": int(cur),
                "committed_side": 0,
                "long_legs": long_count, "short_legs": short_count,
                "kind": "flat",
            })
            return

        if target == 1:
            ctx.enter_long(reason=f"MFP: net long ({long_count}>{short_count})")
            self._committed_side = 1
            self._clear_manual_close_guard()
            self._emit_event(ctx, "MFP_ENTER_LONG", {
                "ts": ts, "target": int(target), "prev_side": int(cur),
                "committed_side": 1,
                "long_legs": long_count, "short_legs": short_count,
            })
        elif target == -1:
            ctx.enter_short(reason=f"MFP: net short ({short_count}>{long_count})")
            self._committed_side = -1
            self._clear_manual_close_guard()
            self._emit_event(ctx, "MFP_ENTER_SHORT", {
                "ts": ts, "target": int(target), "prev_side": int(cur),
                "committed_side": -1,
                "long_legs": long_count, "short_legs": short_count,
            })

    def _build_snapshot(self) -> dict[str, Any]:
        snap = super()._build_snapshot()
        snap["manual_close_guard"] = {
            "blocked_side": int(self._manual_close_block_side),
            "reported": bool(self._manual_close_block_reported),
        }
        return snap

    def _restore_from_snapshot(self, snap: dict[str, Any]) -> bool:
        ok = super()._restore_from_snapshot(snap)
        if not ok:
            return False
        st = snap.get("manual_close_guard") or {}
        if not isinstance(st, dict):
            self._clear_manual_close_guard()
            return True
        side = int(self._safe_float(st.get("blocked_side", 0), 0.0))
        self._set_manual_close_guard(side)
        self._manual_close_block_reported = bool(st.get("reported", False))
        return True
