"""RLS Forecast Mean Reversion 전략.

Recursive Least Squares 알고리즘을 사용한 적응형 선형 회귀로 가격 추세를 추정하고,
동적 밴드를 이용한 평균 회귀(Mean Reversion) 시그널로 롱/숏 매매.

진입:
- 롱: 가격이 하단 밴드 아래로 이탈 후 밴드 안쪽으로 복귀 시 BUY
- 숏: 가격이 상단 밴드 위로 이탈 후 밴드 안쪽으로 복귀 시 SELL

청산:
- 롱/숏 모두 RLS 평균선 도달 시 (take-profit target)

참고: LuxAlgo의 Recursive Least Squares Forecast 지표 로직 기반.
"""

from __future__ import annotations

import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


def _last_non_nan(values: Any) -> float | None:
    try:
        n = int(getattr(values, "size", len(values)))
    except Exception:  # noqa: BLE001
        return None
    for i in range(n - 1, -1, -1):
        try:
            v = float(values[i])
        except Exception:  # noqa: BLE001
            continue
        if not math.isnan(v):
            return v
    return None


class RlsForecastMeanReversionStrategy(Strategy):
    """RLS Forecast Mean Reversion 전략.

    Recursive Least Squares 적응형 필터로 실시간 추세를 추정하고,
    표준편차 기반 동적 밴드의 평균 회귀 시그널로 진입/청산.

    - Forgetting Factor(λ): 모델의 메모리 제어 (낮을수록 최신 데이터에 민감)
    - Band Multiplier: 밴드 폭 조절 (표준편차 배수)
    """

    INDICATOR_NAME = "RLS_FORECAST"

    def __init__(
        self,
        forgetting_factor: float = 0.97,
        band_multiplier: float = 2.0,
    ) -> None:
        super().__init__()
        if not 0.0 < forgetting_factor <= 1.0:
            raise ValueError("forgetting_factor must be in (0, 1]")
        if band_multiplier <= 0:
            raise ValueError("band_multiplier must be > 0")

        self.forgetting_factor = float(forgetting_factor)
        self.band_multiplier = float(band_multiplier)

        self.prev_close: float | None = None
        self.prev_rls_mean: float | None = None
        self.prev_upper: float | None = None
        self.prev_lower: float | None = None
        self.is_closing: bool = False

        self.params = {
            "forgetting_factor": self.forgetting_factor,
            "band_multiplier": self.band_multiplier,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {
                "forgetting_factor": self.forgetting_factor,
                "band_multiplier": self.band_multiplier,
            },
        }

    def initialize(self, ctx: StrategyContext) -> None:
        self._register_rls_indicator(ctx)
        self.prev_close = None
        self.prev_rls_mean = None
        self.prev_upper = None
        self.prev_lower = None
        self.is_closing = False

    def _register_rls_indicator(self, ctx: StrategyContext) -> None:
        """커스텀 RLS Forecast 인디케이터를 등록한다.

        전체 close 이력에 대해 RLS 알고리즘을 적용하여
        rls_mean, upper, lower, slope를 dict로 반환.
        """

        def _rls_compute(inner_ctx: Any, *args: Any, **kwargs: Any) -> dict[str, float]:
            nan_result = {
                "rls_mean": math.nan,
                "upper": math.nan,
                "lower": math.nan,
                "slope": math.nan,
            }

            ff = kwargs.get("forgetting_factor", 0.97)
            mult = kwargs.get("band_multiplier", 2.0)

            inputs_fn = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
            if not callable(inputs_fn):
                return nan_result

            raw = inputs_fn()
            close_raw = raw.get("close", [])
            if close_raw is None or len(close_raw) < 2:
                return nan_result

            prices: list[float] = []
            for c in close_raw:
                try:
                    prices.append(float(c))
                except (TypeError, ValueError):
                    prices.append(math.nan)

            lam = float(ff)
            delta = 1000.0
            p00, p01, p10, p11 = delta, 0.0, 0.0, delta
            theta0, theta1 = prices[0] if math.isfinite(prices[0]) else 0.0, 0.0
            error_var = 0.0

            for i, y in enumerate(prices):
                if not math.isfinite(y):
                    continue

                x0, x1 = 1.0, float(i)
                y_hat = theta0 * x0 + theta1 * x1
                e = y - y_hat

                px0 = p00 * x0 + p01 * x1
                px1 = p10 * x0 + p11 * x1
                xpx = x0 * px0 + x1 * px1
                denom = lam + xpx

                if abs(denom) < 1e-15:
                    continue

                k0 = px0 / denom
                k1 = px1 / denom

                theta0 += k0 * e
                theta1 += k1 * e

                xp0 = x0 * p00 + x1 * p10
                xp1 = x0 * p01 + x1 * p11

                p00 = (p00 - k0 * xp0) / lam
                p01 = (p01 - k0 * xp1) / lam
                p10 = (p10 - k1 * xp0) / lam
                p11 = (p11 - k1 * xp1) / lam

                error_var = lam * error_var + (1.0 - lam) * e * e

            last_i = float(len(prices) - 1)
            rls_mean = theta0 + theta1 * last_i
            slope = theta1
            std = math.sqrt(max(error_var, 0.0))
            upper = rls_mean + mult * std
            lower = rls_mean - mult * std

            return {
                "rls_mean": rls_mean,
                "upper": upper,
                "lower": lower,
                "slope": slope,
            }

        ctx.register_indicator(self.INDICATOR_NAME, _rls_compute)

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        if ctx.get_open_orders():
            return

        if not bool(bar.get("is_new_bar", True)):
            return

        result = ctx.get_indicator(
            self.INDICATOR_NAME,
            forgetting_factor=self.forgetting_factor,
            band_multiplier=self.band_multiplier,
        )

        if not isinstance(result, dict):
            return

        rls_mean = result.get("rls_mean", math.nan)
        upper = result.get("upper", math.nan)
        lower = result.get("lower", math.nan)

        if not all(math.isfinite(v) for v in (rls_mean, upper, lower)):
            return

        close = float(bar.get("close", math.nan))
        if not math.isfinite(close):
            return

        if (
            self.prev_close is None
            or self.prev_rls_mean is None
            or self.prev_upper is None
            or self.prev_lower is None
        ):
            self.prev_close = close
            self.prev_rls_mean = rls_mean
            self.prev_upper = upper
            self.prev_lower = lower
            return

        # ===== 롱 청산: 가격이 RLS 평균선 상향 도달 (take-profit) =====
        if ctx.position_size > 0 and not self.is_closing:
            if self.prev_close < self.prev_rls_mean and close >= rls_mean:
                self.is_closing = True
                ctx.close_position(
                    reason=f"Exit Long (price {close:.2f} reached RLS mean {rls_mean:.2f})",
                )
                self._update_prev(close, rls_mean, upper, lower)
                return

        # ===== 숏 청산: 가격이 RLS 평균선 하향 도달 (take-profit) =====
        if ctx.position_size < 0 and not self.is_closing:
            if self.prev_close > self.prev_rls_mean and close <= rls_mean:
                self.is_closing = True
                ctx.close_position(
                    reason=f"Exit Short (price {close:.2f} reached RLS mean {rls_mean:.2f})",
                )
                self._update_prev(close, rls_mean, upper, lower)
                return

        # ===== 롱 진입: 하단 밴드 이탈 후 복귀 (Mean Reversion BUY) =====
        if ctx.position_size == 0:
            if self.prev_close < self.prev_lower and close >= lower:
                ctx.enter_long(
                    reason=f"Mean Reversion BUY (price {close:.2f} > lower band {lower:.2f})",
                )

        # ===== 숏 진입: 상단 밴드 이탈 후 복귀 (Mean Reversion SELL) =====
        if ctx.position_size == 0:
            if self.prev_close > self.prev_upper and close <= upper:
                ctx.enter_short(
                    reason=f"Mean Reversion SELL (price {close:.2f} < upper band {upper:.2f})",
                )

        self._update_prev(close, rls_mean, upper, lower)

    def _update_prev(
        self,
        close: float,
        rls_mean: float,
        upper: float,
        lower: float,
    ) -> None:
        self.prev_close = close
        self.prev_rls_mean = rls_mean
        self.prev_upper = upper
        self.prev_lower = lower
