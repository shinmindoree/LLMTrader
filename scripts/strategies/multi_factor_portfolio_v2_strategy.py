"""MFP V2: adds portfolio-level profit-protect exit on top of base MFP.

Rule added (base exits remain unchanged):
- After position entry, once unrealized PnL reaches >= 1.0% of deployed capital,
  arm protection.
- If armed and unrealized PnL falls to <= 0.08% (round-trip fee level),
  close the position immediately.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Alias parent name so auto-loader does not pick the imported base class first.
from multi_factor_portfolio_strategy import (  # noqa: E402
    MultiFactorPortfolioStrategy as _MFPBase,
)


DEFAULTS: dict[str, Any] = {
    "profit_lock_activation_pct": 0.01,   # +1.00%
    "profit_lock_exit_pct": 0.0008,       # +0.08% (entry+exit fee)
}


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "profit_lock_activation_pct",
        "type": "float",
        "label": "Profit-lock activation pct (fraction of deployed capital)",
    },
    {
        "name": "profit_lock_exit_pct",
        "type": "float",
        "label": "Profit-lock exit pct (fraction of deployed capital)",
    },
]


class MultiFactorPortfolioV2Strategy(_MFPBase):
    """Base MFP + portfolio-level profit-protect exit."""

    def __init__(self, **kwargs: Any) -> None:
        my_p = {k: kwargs.pop(k, v) for k, v in DEFAULTS.items()}
        super().__init__(**kwargs)
        self.profit_lock_activation_pct = float(my_p["profit_lock_activation_pct"])
        self.profit_lock_exit_pct = float(my_p["profit_lock_exit_pct"])
        self.params = {**self.params, **my_p}

        # Track whether the current position has already reached activation.
        self._profit_lock_side: int = 0
        self._profit_lock_armed: bool = False
        self._profit_lock_peak_ratio: float = 0.0

    def _reset_profit_lock(self) -> None:
        self._profit_lock_side = 0
        self._profit_lock_armed = False
        self._profit_lock_peak_ratio = 0.0

    @staticmethod
    def _safe_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _current_side(self, ctx: Any) -> int:
        size = self._safe_float(getattr(ctx, "position_size", 0.0), 0.0)
        if abs(size) < 1e-12:
            return 0
        return 1 if size > 0.0 else -1

    def _deployed_capital(self, ctx: Any, size: float, entry_price: float) -> float:
        # Prefer entry-balance semantics when the context exposes it.
        entry_balance = self._safe_float(getattr(ctx, "position_entry_balance", 0.0), 0.0)
        if entry_balance > 0.0:
            return entry_balance

        pos = getattr(ctx, "position", None)
        if pos is not None:
            eb = self._safe_float(getattr(pos, "entry_balance", 0.0), 0.0)
            if eb > 0.0:
                return eb

        # Fallback 1: derive from notional / leverage if available.
        leverage = self._safe_float(getattr(ctx, "leverage", 0.0), 0.0)
        notional = abs(size) * max(entry_price, 0.0)
        if leverage > 0.0 and notional > 0.0:
            return notional / leverage

        # Fallback 2: use notional itself.
        return notional

    def _current_pnl_ratio(self, ctx: Any) -> float:
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

    def _reconcile(
        self,
        ctx: Any,
        target: int,
        long_count: int,
        short_count: int,
        ts: int,
    ) -> None:
        side = self._current_side(ctx)
        if side == 0:
            self._reset_profit_lock()
            super()._reconcile(ctx, target, long_count, short_count, ts)
            return

        if side != self._profit_lock_side:
            self._profit_lock_side = side
            self._profit_lock_armed = False
            self._profit_lock_peak_ratio = 0.0

        ratio = self._current_pnl_ratio(ctx)
        if ratio > self._profit_lock_peak_ratio:
            self._profit_lock_peak_ratio = ratio

        activation = float(self.profit_lock_activation_pct)
        exit_level = float(self.profit_lock_exit_pct)
        if activation > 0.0 and ratio >= activation:
            self._profit_lock_armed = True

        if (
            self._profit_lock_armed
            and exit_level >= 0.0
            and ratio <= exit_level
        ):
            prev_side = int(self._committed_side)
            reason = (
                "MFP V2: profit-protect exit "
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
            self._emit_event(ctx, "MFP_PROFIT_PROTECT_EXIT", {
                "ts": ts,
                "prev_side": prev_side,
                "long_legs": int(long_count),
                "short_legs": int(short_count),
                "pnl_ratio": ratio,
                "peak_ratio": float(self._profit_lock_peak_ratio),
                "activation_ratio": float(activation),
                "exit_ratio": float(exit_level),
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
            return

        super()._reconcile(ctx, target, long_count, short_count, ts)

    # ---- snapshot persistence: include profit-lock tracker -----------------
    def _build_snapshot(self) -> dict[str, Any]:
        snap = super()._build_snapshot()
        snap["profit_lock"] = {
            "side": int(self._profit_lock_side),
            "armed": bool(self._profit_lock_armed),
            "peak_ratio": float(self._profit_lock_peak_ratio),
        }
        return snap

    def _restore_from_snapshot(self, snap: dict[str, Any]) -> bool:
        ok = super()._restore_from_snapshot(snap)
        if not ok:
            return False
        st = snap.get("profit_lock") or {}
        if not isinstance(st, dict):
            self._reset_profit_lock()
            return True
        side = int(self._safe_float(st.get("side", 0), 0.0))
        self._profit_lock_side = 1 if side > 0 else (-1 if side < 0 else 0)
        self._profit_lock_armed = bool(st.get("armed", False))
        self._profit_lock_peak_ratio = self._safe_float(st.get("peak_ratio", 0.0), 0.0)
        return True

