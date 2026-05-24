"""Defensive MFP + Dynamic Kelly position sizing layer.

Subclasses ``MultiFactorPortfolioDefensiveStrategy`` and injects per-entry
position sizing driven by ``DynamicKellyRiskManager``:

* Tracks realised PnL of every closed trade from ``ctx.trades``.
* Tracks running equity peak from ``ctx.total_equity`` to derive current
  MDD %.
* Before each ``_reconcile`` call, mutates ``ctx.fixed_notional`` to
  ``base_notional × kelly_target_leverage`` so the next ``enter_long`` /
  ``enter_short`` uses Kelly-scaled sizing.

Burn-in: until ``kelly_min_trades`` closed trades are seen, the sizer
falls back to ``kelly_burn_in_leverage`` (default 0.10) — which lines up
with the legacy baseline of ``fixed_notional=$1000`` on $10000 capital.

Backtest-only sizing. Live path is unchanged (Kelly logic still runs but
mutating ``ctx.fixed_notional`` on the live context is harmless: the
attribute simply doesn't exist there).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from multi_factor_portfolio_defensive_strategy import (  # noqa: E402
    MultiFactorPortfolioDefensiveStrategy,
)
from strategy.context import StrategyContext  # noqa: E402

from common.dynamic_kelly import DynamicKellyRiskManager  # noqa: E402


KELLY_DEFAULTS: dict[str, Any] = {
    # Sizer policy
    "kelly_min_trades": 30,
    "kelly_burn_in_leverage": 0.10,
    "kelly_fraction": 0.5,            # Half-Kelly
    "kelly_max_leverage": 1.0,
    "kelly_max_allowed_mdd_pct": 10.0,  # in % (matches MDD reported by harness)
    # Sliding window of recent trades used to estimate p / b. ``None`` or
    # 0 = use all closed trades.
    "kelly_window": 100,
    # Base notional in USDT that Kelly target leverage is multiplied
    # against. Default 10_000 lines up with the harness's initial_balance
    # so a kelly_target of 0.10 produces the same per-trade $ as the
    # legacy fixed_notional=$1000 baseline.
    "kelly_base_notional": 10000.0,
}


class MultiFactorPortfolioKellyStrategy(MultiFactorPortfolioDefensiveStrategy):
    """Defensive MFP + dynamic Kelly notional sizing (backtest only)."""

    def __init__(self, **kwargs: Any) -> None:
        my_p = {k: kwargs.pop(k, v) for k, v in KELLY_DEFAULTS.items()}
        super().__init__(**kwargs)
        for k, v in my_p.items():
            setattr(self, k, v)
        self.params = {**self.params, **my_p}

        self._kelly_sizer = DynamicKellyRiskManager(
            min_trades_required=int(self.kelly_min_trades),
            burn_in_leverage=float(self.kelly_burn_in_leverage),
            kelly_fraction=float(self.kelly_fraction),
            max_leverage=float(self.kelly_max_leverage),
        )
        # Equity peak tracker for MDD computation.
        self._equity_peak: float = 0.0
        # Cache the last applied target so debug events don't spam identical values.
        self._last_kelly_target: float = -1.0
        # Diagnostic counters (used by ``finalize_debug``)
        self._kelly_call_count: int = 0
        self._kelly_last_notional: float = 0.0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _collect_recent_pnls(self, ctx: Any) -> list[float]:
        """Net realised PnL per closed trade from ``ctx.trades``.

        Backtest trade rows have ``pnl`` on close events and only
        ``commission`` on open events. We treat ``pnl - commission`` per
        close as the trade's net PnL.
        """
        trades = getattr(ctx, "trades", None) or []
        pnls: list[float] = []
        for t in trades:
            if "pnl" not in t:
                continue  # entry-only event
            try:
                pnl = float(t.get("pnl", 0.0))
                comm = float(t.get("commission", 0.0))
            except (TypeError, ValueError):
                continue
            pnls.append(pnl - comm)
        win = int(self.kelly_window or 0)
        if win > 0 and len(pnls) > win:
            pnls = pnls[-win:]
        return pnls

    def _current_mdd_pct(self, ctx: Any) -> float:
        try:
            eq = float(getattr(ctx, "total_equity", 0.0) or 0.0)
        except (TypeError, ValueError):
            eq = 0.0
        if eq > self._equity_peak:
            self._equity_peak = eq
        if self._equity_peak <= 0:
            return 0.0
        return max(0.0, (self._equity_peak - eq) / self._equity_peak * 100.0)

    def _apply_kelly_sizing(self, ctx: Any) -> float:
        """Recompute Kelly target and mutate ctx.fixed_notional accordingly.

        Returns the target leverage actually applied (for audit/debug).
        """
        # Only sizes the backtest context; live context has no fixed_notional.
        if not hasattr(ctx, "fixed_notional"):
            return float("nan")

        pnls = self._collect_recent_pnls(ctx)
        mdd_pct = self._current_mdd_pct(ctx)
        try:
            target = self._kelly_sizer.get_target_leverage(
                current_mdd_pct=float(mdd_pct),
                max_allowed_mdd_pct=float(self.kelly_max_allowed_mdd_pct),
                trades=pnls,
            )
        except Exception:  # noqa: BLE001 — defensive: never block reconcile
            target = float(self._kelly_sizer.burn_in_leverage)

        # Map target leverage → USDT notional.
        notional = float(self.kelly_base_notional) * float(target)
        if not math.isfinite(notional) or notional <= 0:
            # Zero-notional → BacktestContext.calc_entry_quantity returns 0,
            # so no entry will fire on this reconcile pass. We still set a
            # tiny positive value (1e-9) over setting None to keep the
            # branch "fixed-notional mode" instead of falling back to the
            # equity-based path.
            ctx.fixed_notional = 1e-9
        else:
            ctx.fixed_notional = notional

        self._kelly_call_count += 1
        self._kelly_last_notional = float(ctx.fixed_notional)
        self._last_kelly_target = float(target)
        return float(target)

    # ------------------------------------------------------------------
    # Override reconcile to size BEFORE entries
    # ------------------------------------------------------------------
    def _reconcile(self, ctx: StrategyContext, target: int, long_count: int,
                   short_count: int, ts: int) -> None:
        try:
            self._apply_kelly_sizing(ctx)
        except Exception:  # noqa: BLE001
            pass
        super()._reconcile(ctx, target, long_count, short_count, ts)

    def finalize_debug(self) -> dict[str, Any]:
        """Diagnostic snapshot for the harness's ``[strategy.debug]`` line."""
        return {
            "kelly_call_count": int(self._kelly_call_count),
            "kelly_last_notional": round(float(self._kelly_last_notional), 2),
            "kelly_last_target": round(float(self._last_kelly_target), 4),
            "equity_peak": round(float(self._equity_peak), 2),
        }
