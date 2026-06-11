"""BTC MFP with conviction-proportional fractional exposure.

This strategy intentionally leaves ``multi_factor_portfolio_strategy.py`` frozen.
It reuses the parent MFP data loaders, signal families, leg state machine, and
TP/SL/TIME handling, but overrides only the final portfolio reconciliation step:

    target_exposure = (long_legs - short_legs) / total_legs

So a strong 50L/5S vote in a 60-leg pool maps to +75% exposure, while an
ambiguous 31L/29S vote maps to about +3% exposure instead of a full-size long.
For the current 17-leg BTC MFP, each net vote contributes about 5.88% exposure.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import multi_factor_portfolio_strategy as _mfp  # noqa: E402

from strategy.context import StrategyContext  # noqa: E402

STRATEGY_PARAMS: dict[str, Any] = {
    "fractional_exposure_scale": 1.0,
    "fractional_exposure_max": 1.0,
    "fractional_exposure_step": 0.0,
}


STRATEGY_PARAM_SCHEMA: dict[str, dict[str, Any]] = {
    "fractional_exposure_scale": {
        "name": "fractional_exposure_scale",
        "type": "number",
        "min": 0.0,
        "max": 5.0,
        "step": 0.1,
        "label": "분수 노출 배율",
        "description": "투표 비율로 계산한 기본 노출을 몇 배로 키울지 정합니다.",
        "group": "리스크 관리 (Risk)",
    },
    "fractional_exposure_max": {
        "name": "fractional_exposure_max",
        "type": "number",
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "label": "최대 절대 노출",
        "description": "scale 적용 후에도 넘을 수 없는 포지션 노출 상한입니다.",
        "group": "리스크 관리 (Risk)",
    },
    "fractional_exposure_step": {
        "name": "fractional_exposure_step",
        "type": "number",
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "label": "노출 버킷 단위",
        "description": "0이면 투표 비율을 그대로 쓰고, 0보다 크면 해당 단위로 반올림합니다.",
        "group": "리스크 관리 (Risk)",
    },
}


class MultiFactorPortfolioFractionalStrategy(_mfp.MultiFactorPortfolioStrategy):
    """Base BTC MFP sized by vote margin instead of binary majority."""

    def __init__(self, **kwargs: Any) -> None:
        fractional_params = {k: kwargs.pop(k, v) for k, v in STRATEGY_PARAMS.items()}
        super().__init__(**kwargs)

        self.fractional_exposure_scale = float(fractional_params["fractional_exposure_scale"])
        self.fractional_exposure_max = float(fractional_params["fractional_exposure_max"])
        self.fractional_exposure_step = float(fractional_params["fractional_exposure_step"])
        if self.fractional_exposure_scale < 0.0:
            raise ValueError("fractional_exposure_scale must be >= 0")
        if not 0.0 <= self.fractional_exposure_max <= 1.0:
            raise ValueError("fractional_exposure_max must be between 0 and 1")
        if self.fractional_exposure_step < 0.0:
            raise ValueError("fractional_exposure_step must be >= 0")

        self.params = {**self.params, **fractional_params}
        self._committed_exposure: float = 0.0

    def _target_exposure(self, long_count: int, short_count: int) -> float:
        n_legs = len(self._legs)
        if n_legs <= 0:
            return 0.0

        raw = (long_count - short_count) / n_legs
        raw *= self.fractional_exposure_scale
        cap = self.fractional_exposure_max
        raw = max(-cap, min(cap, raw))

        step = self.fractional_exposure_step
        if step > 0.0:
            raw = round(raw / step) * step
            raw = max(-cap, min(cap, raw))
        if abs(raw) < 1e-12:
            return 0.0
        return float(raw)

    @staticmethod
    def _position_size(ctx: StrategyContext) -> float:
        pos = getattr(ctx, "position", None)
        value = (
            getattr(pos, "size", 0.0)
            if pos is not None
            else getattr(ctx, "position_size", 0.0)
        )
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _side_from_value(value: float) -> int:
        if value > 1e-12:
            return 1
        if value < -1e-12:
            return -1
        return 0

    def _sync_committed_from_context(self, ctx: StrategyContext, ts: int) -> float:
        size = self._position_size(ctx)
        actual_side = self._side_from_value(size)
        if actual_side == 0:
            if self._committed_side != 0 or abs(self._committed_exposure) > 1e-12:
                self._emit_event(ctx, "MFP_FRACTIONAL_CTX_RESYNC", {
                    "ts": ts,
                    "cached_side": int(self._committed_side),
                    "cached_exposure": float(self._committed_exposure),
                    "actual_side": 0,
                })
            self._committed_side = 0
            self._committed_exposure = 0.0
            return size

        if actual_side != self._committed_side:
            self._emit_event(ctx, "MFP_FRACTIONAL_CTX_RESYNC", {
                "ts": ts,
                "cached_side": int(self._committed_side),
                "cached_exposure": float(self._committed_exposure),
                "actual_side": int(actual_side),
            })
            self._committed_side = actual_side
        return size

    def _enter_target(
        self,
        ctx: StrategyContext,
        target_exposure: float,
        long_count: int,
        short_count: int,
    ) -> None:
        magnitude = abs(target_exposure)
        if target_exposure > 0.0:
            ctx.enter_long(
                reason=(
                    f"MFP fractional long exp={magnitude:.4f} "
                    f"({long_count}L/{short_count}S/{len(self._legs)} legs)"
                ),
                entry_pct=magnitude,
            )
        elif target_exposure < 0.0:
            ctx.enter_short(
                reason=(
                    f"MFP fractional short exp={magnitude:.4f} "
                    f"({long_count}L/{short_count}S/{len(self._legs)} legs)"
                ),
                entry_pct=magnitude,
            )

    def _reconcile(
        self,
        ctx: StrategyContext,
        target: int,
        long_count: int,
        short_count: int,
        ts: int,
    ) -> None:
        target_exposure = self._target_exposure(long_count, short_count)
        target_side = self._side_from_value(target_exposure)
        current_size = self._sync_committed_from_context(ctx, ts)
        current_side = self._side_from_value(current_size)
        previous_exposure = self._committed_exposure

        if abs(target_exposure - previous_exposure) < 1e-12:
            return

        if current_side != 0 and target_side == 0:
            ctx.close_position(reason=f"MFP fractional flat ({long_count}L/{short_count}S)")
        elif current_side not in (0, target_side):
            flip_position = getattr(ctx, "flip_position", None)
            if callable(flip_position):
                flip_position(
                    target_side=target_side,
                    close_reason=(
                        f"MFP fractional flip {previous_exposure:+.4f}"
                        f"->{target_exposure:+.4f}"
                    ),
                    entry_reason=(
                        f"MFP fractional {'long' if target_side > 0 else 'short'} "
                        f"exp={abs(target_exposure):.4f} "
                        f"({long_count}L/{short_count}S/{len(self._legs)} legs)"
                    ),
                    entry_pct=abs(target_exposure),
                )
            else:
                ctx.close_position(
                    reason=(
                        f"MFP fractional flip {previous_exposure:+.4f}"
                        f"->{target_exposure:+.4f}"
                    )
                )
                self._enter_target(ctx, target_exposure, long_count, short_count)
        else:
            if current_side != 0:
                ctx.close_position(
                    reason=(
                        f"MFP fractional resize {previous_exposure:+.4f}"
                        f"->{target_exposure:+.4f}"
                    )
                )
            self._enter_target(ctx, target_exposure, long_count, short_count)

        self._committed_side = target_side
        self._committed_exposure = target_exposure
        self._emit_event(ctx, "MFP_FRACTIONAL_EXPOSURE", {
            "ts": ts,
            "parent_target": int(target),
            "target": int(target_side),
            "target_exposure": float(target_exposure),
            "prev_exposure": float(previous_exposure),
            "committed_side": int(self._committed_side),
            "long_legs": int(long_count),
            "short_legs": int(short_count),
            "total_legs": int(len(self._legs)),
        })

    def _build_snapshot(self) -> dict[str, Any]:
        snap = super()._build_snapshot()
        snap["fractional_exposure"] = {
            "committed_exposure": float(self._committed_exposure),
        }
        return snap

    def _restore_from_snapshot(self, snap: dict[str, Any]) -> bool:
        ok = super()._restore_from_snapshot(snap)
        if not ok:
            return False

        state = snap.get("fractional_exposure")
        if isinstance(state, dict):
            try:
                self._committed_exposure = float(state.get("committed_exposure", 0.0) or 0.0)
            except (TypeError, ValueError):
                self._committed_exposure = 0.0
        else:
            self._committed_exposure = 0.0
        return True
