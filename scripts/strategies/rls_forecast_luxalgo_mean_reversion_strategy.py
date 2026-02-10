"""RLS Forecast (LuxAlgo style) Mean Reversion 전략.

설명:
- Recursive Least Squares(RLS)로 적응형 선형 추세(평균선)를 실시간 추정
- 잔차 표준편차 기반 동적 밴드(upper/lower)로 과확장 구간 탐지
- 밴드 재진입 시 평균회귀 진입, RLS 평균선 도달 시 청산
- Forecast Horizon 기반 Ghost projection(미래 평균/밴드)도 함께 계산

진입:
- 롱: 직전 봉이 하단 밴드 아래, 현재 봉이 하단 밴드 안쪽으로 복귀
- 숏: 직전 봉이 상단 밴드 위, 현재 봉이 상단 밴드 안쪽으로 복귀

청산:
- 롱/숏 모두 가격이 RLS 평균선에 도달하면 청산
"""

from __future__ import annotations

import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


class RlsForecastLuxalgoMeanReversionStrategy(Strategy):
    """RLS Forecast 기반 평균회귀 전략."""

    INDICATOR_NAME = "RLS_FORECAST_LUX"

    def __init__(
        self,
        forgetting_factor: float = 0.97,
        band_multiplier: float = 2.0,
        forecast_horizon: int = 10,
    ) -> None:
        super().__init__()
        if not 0.0 < forgetting_factor <= 1.0:
            raise ValueError("forgetting_factor must be in (0, 1]")
        if band_multiplier <= 0.0:
            raise ValueError("band_multiplier must be > 0")
        if forecast_horizon < 1:
            raise ValueError("forecast_horizon must be >= 1")

        self.forgetting_factor = float(forgetting_factor)
        self.band_multiplier = float(band_multiplier)
        self.forecast_horizon = int(forecast_horizon)

        self.prev_close: float | None = None
        self.prev_rls_mean: float | None = None
        self.prev_upper: float | None = None
        self.prev_lower: float | None = None
        self.is_closing: bool = False

        self.params = {
            "forgetting_factor": self.forgetting_factor,
            "band_multiplier": self.band_multiplier,
            "forecast_horizon": self.forecast_horizon,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {
                "forgetting_factor": self.forgetting_factor,
                "band_multiplier": self.band_multiplier,
                "forecast_horizon": self.forecast_horizon,
            },
        }

    def initialize(self, ctx: StrategyContext) -> None:
        self._register_rls_forecast_indicator(ctx)
        self.prev_close = None
        self.prev_rls_mean = None
        self.prev_upper = None
        self.prev_lower = None
        self.is_closing = False

    def _register_rls_forecast_indicator(self, ctx: StrategyContext) -> None:
        """RLS Forecast 커스텀 인디케이터를 등록한다."""

        def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> dict[str, float]:
            del args  # 커스텀 인디케이터는 keyword 인자만 허용
            nan_result = {
                "rls_mean": math.nan,
                "upper": math.nan,
                "lower": math.nan,
                "slope": math.nan,
                "ghost_mean": math.nan,
                "ghost_upper": math.nan,
                "ghost_lower": math.nan,
            }

            lam = float(kwargs.get("forgetting_factor", 0.97))
            mult = float(kwargs.get("band_multiplier", 2.0))
            horizon = int(kwargs.get("forecast_horizon", 10))

            if not (0.0 < lam <= 1.0) or mult <= 0.0 or horizon < 1:
                return nan_result

            inputs_fn = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
            if not callable(inputs_fn):
                return nan_result

            raw = inputs_fn()
            close_raw = raw.get("close", [])
            if close_raw is None or len(close_raw) < 3:
                return nan_result

            prices: list[float] = []
            for value in close_raw:
                try:
                    prices.append(float(value))
                except (TypeError, ValueError):
                    prices.append(math.nan)

            delta = 1000.0
            p00, p01, p10, p11 = delta, 0.0, 0.0, delta
            theta0 = prices[0] if math.isfinite(prices[0]) else 0.0
            theta1 = 0.0

            error_var = 0.0
            last_index = -1

            for i, y in enumerate(prices):
                if not math.isfinite(y):
                    continue

                x0, x1 = 1.0, float(i)
                y_hat = theta0 * x0 + theta1 * x1
                err = y - y_hat

                px0 = p00 * x0 + p01 * x1
                px1 = p10 * x0 + p11 * x1
                xpx = x0 * px0 + x1 * px1
                denom = lam + xpx

                if abs(denom) < 1e-15:
                    continue

                k0 = px0 / denom
                k1 = px1 / denom

                theta0 += k0 * err
                theta1 += k1 * err

                xp0 = x0 * p00 + x1 * p10
                xp1 = x0 * p01 + x1 * p11

                p00 = (p00 - k0 * xp0) / lam
                p01 = (p01 - k0 * xp1) / lam
                p10 = (p10 - k1 * xp0) / lam
                p11 = (p11 - k1 * xp1) / lam

                error_var = lam * error_var + (1.0 - lam) * (err * err)
                last_index = i

            if last_index < 0:
                return nan_result

            now_x = float(last_index)
            future_x = float(last_index + horizon)

            rls_mean = theta0 + theta1 * now_x
            slope = theta1

            residual_std = math.sqrt(max(error_var, 0.0))
            upper = rls_mean + mult * residual_std
            lower = rls_mean - mult * residual_std

            ghost_mean = theta0 + theta1 * future_x
            # 미래 예측 분산: 관측오차 + 상태 불확실성(x^T P x)
            pred_var = max(
                error_var + (p00 + 2.0 * future_x * p01 + (future_x * future_x) * p11),
                0.0,
            )
            ghost_std = math.sqrt(pred_var)
            ghost_upper = ghost_mean + mult * ghost_std
            ghost_lower = ghost_mean - mult * ghost_std

            return {
                "rls_mean": rls_mean,
                "upper": upper,
                "lower": lower,
                "slope": slope,
                "ghost_mean": ghost_mean,
                "ghost_upper": ghost_upper,
                "ghost_lower": ghost_lower,
            }

        ctx.register_indicator(self.INDICATOR_NAME, _indicator)

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        # 라이브 중복 주문 방지
        if ctx.get_open_orders():
            return

        # 확정 봉 기준으로만 신호 판단
        if not bool(bar.get("is_new_bar", True)):
            return

        result = ctx.get_indicator(
            self.INDICATOR_NAME,
            forgetting_factor=self.forgetting_factor,
            band_multiplier=self.band_multiplier,
            forecast_horizon=self.forecast_horizon,
        )
        if not isinstance(result, dict):
            return

        rls_mean = float(result.get("rls_mean", math.nan))
        upper = float(result.get("upper", math.nan))
        lower = float(result.get("lower", math.nan))
        slope = float(result.get("slope", math.nan))
        ghost_mean = float(result.get("ghost_mean", math.nan))

        if not all(math.isfinite(v) for v in (rls_mean, upper, lower, slope, ghost_mean)):
            return

        close = float(bar.get("bar_close", bar.get("close", math.nan)))
        if not math.isfinite(close):
            return

        if (
            self.prev_close is None
            or self.prev_rls_mean is None
            or self.prev_upper is None
            or self.prev_lower is None
        ):
            self._update_prev(close, rls_mean, upper, lower)
            return

        # ===== 평균선 타겟 청산 =====
        if ctx.position_size > 0 and not self.is_closing:
            if self.prev_close < self.prev_rls_mean and close >= rls_mean:
                self.is_closing = True
                ctx.close_position(
                    reason=(
                        f"Exit Long: mean touch ({close:.2f} >= {rls_mean:.2f}), "
                        f"slope={slope:.5f}, ghost={ghost_mean:.2f}"
                    ),
                )
                self._update_prev(close, rls_mean, upper, lower)
                return

        if ctx.position_size < 0 and not self.is_closing:
            if self.prev_close > self.prev_rls_mean and close <= rls_mean:
                self.is_closing = True
                ctx.close_position(
                    reason=(
                        f"Exit Short: mean touch ({close:.2f} <= {rls_mean:.2f}), "
                        f"slope={slope:.5f}, ghost={ghost_mean:.2f}"
                    ),
                )
                self._update_prev(close, rls_mean, upper, lower)
                return

        # ===== Mean Reversion 진입(밴드 재진입) =====
        if ctx.position_size == 0:
            if self.prev_close < self.prev_lower and close >= lower:
                ctx.enter_long(
                    reason=(
                        f"BUY re-entry: {close:.2f} back above lower {lower:.2f} "
                        f"(mean={rls_mean:.2f}, slope={slope:.5f}, ghost={ghost_mean:.2f})"
                    ),
                )

        if ctx.position_size == 0:
            if self.prev_close > self.prev_upper and close <= upper:
                ctx.enter_short(
                    reason=(
                        f"SELL re-entry: {close:.2f} back below upper {upper:.2f} "
                        f"(mean={rls_mean:.2f}, slope={slope:.5f}, ghost={ghost_mean:.2f})"
                    ),
                )

        self._update_prev(close, rls_mean, upper, lower)

    def _update_prev(self, close: float, rls_mean: float, upper: float, lower: float) -> None:
        self.prev_close = close
        self.prev_rls_mean = rls_mean
        self.prev_upper = upper
        self.prev_lower = lower
