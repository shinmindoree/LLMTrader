"""MAD Bollinger Bands + Loops 기반 롱/숏 전략.

개요:
- Bollinger 스타일 밴드를 표준편차 대신 MAD(Mean Absolute Deviation)로 계산해 변동성에 덜 민감하게 만든다.
- MAD로 정규화한 가격(= deviation / MAD)의 방향 일관성을 for-loop 비교로 점수화해 모멘텀을 판단한다.
- 두 컴포넌트 신호(+1/-1)를 결합(평균)해 하이브리드 방향성을 만든다.

규칙(기본):
- Band 신호(+1/-1): close가 상단 밴드 위면 +1, 하단 밴드 아래면 -1, 그 외에는 MA 위/아래로 +1/-1.
- Momentum 신호(+1/-1/0): 최근 loop_length개 과거 대비 현재 MAD-정규화 값이 더 크면 +1, 작으면 -1로 합산.
- 매수(롱 진입): Band=+1 AND Momentum=+1
- 매도(숏 진입): Band=-1 AND Momentum=-1
- 롱 청산: Band=-1 AND Momentum=-1
- 숏 청산: Band=+1 AND Momentum=+1

참고:
- StopLoss/수량 산정은 컨텍스트(ctx)가 담당 → 전략은 신호만 생성
- 신호 판단/상태 업데이트는 `bar["is_new_bar"] == True` 에서만 수행 (백테스트 stoploss 시뮬레이션 호환)
- 라이브 중복 주문 방지: 미체결 주문이 있으면 신호 무시(`ctx.get_open_orders()` 가드)
"""

from __future__ import annotations

import math
from statistics import fmean
from typing import Any, Sequence

from strategy.base import Strategy
from strategy.context import StrategyContext


_OUT_KEYS = (
    "ma",
    "mad",
    "upper",
    "lower",
    "band_signal",
    "momentum_score",
    "momentum_signal",
    "hybrid_score",
    "hybrid_signal",
)


def _nan_outputs() -> dict[str, float]:
    return {k: float("nan") for k in _OUT_KEYS}


def _is_finite(x: float) -> bool:
    return math.isfinite(float(x))


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    """EMA 시리즈(길이=len(values))를 반환. period-1까지는 NaN."""
    if period <= 1:
        return [float("nan")] * len(values)
    n = len(values)
    if n == 0:
        return []
    alpha = 2.0 / (float(period) + 1.0)
    out = [float("nan")] * n
    if n < period:
        # 데이터가 부족하면 초기값 기반 EMA를 채워주되, 일관성을 위해 모두 NaN 유지
        return out
    ema = fmean(values[:period])
    out[period - 1] = float(ema)
    for i in range(period, n):
        ema = alpha * float(values[i]) + (1.0 - alpha) * float(ema)
        out[i] = float(ema)
    return out


def _mean_abs_dev(values: Sequence[float], center: float) -> float:
    if not values:
        return float("nan")
    c = float(center)
    return float(fmean([abs(float(v) - c) for v in values]))


def _mad_bollinger_loops_indicator(
    inner_ctx: Any,
    *,
    length: int = 20,
    multiplier: float = 2.0,
    loop_length: int = 10,
    eps: float = 1e-12,
    **_kwargs: Any,
) -> dict[str, float]:
    """MAD Bollinger Bands + Loops 지표.

    Returns:
        dict with keys:
        - ma, mad, upper, lower
        - band_signal (+1/-1)
        - momentum_score ([-loop_length, loop_length])
        - momentum_signal (+1/-1/0)
        - hybrid_score ((band_signal + momentum_signal)/2)
        - hybrid_signal (sign of hybrid_score)
    """
    try:
        length_i = int(length)
        loop_i = int(loop_length)
        mult = float(multiplier)
        eps_f = float(eps)
    except Exception:  # noqa: BLE001
        return _nan_outputs()

    if length_i <= 1 or loop_i <= 0 or mult <= 0:
        return _nan_outputs()

    inputs_fn = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
    if not callable(inputs_fn):
        return _nan_outputs()

    inputs = inputs_fn()
    closes_raw = inputs.get("close") if isinstance(inputs, dict) else None
    closes = [float(x) for x in (closes_raw or [])]
    n = len(closes)

    # 모멘텀 점수 계산을 위해: EMA/MAD가 안정적으로 계산되는 구간(>= length-1)에서
    # loop_length개의 과거와 비교할 수 있어야 한다.
    min_bars = length_i + loop_i
    if n < min_bars:
        return _nan_outputs()

    ema = _ema_series(closes, length_i)
    ma = float(ema[-1])
    if not _is_finite(ma):
        return _nan_outputs()

    window = closes[-length_i:]
    mad = _mean_abs_dev(window, ma)
    if not _is_finite(mad):
        return _nan_outputs()

    upper = ma + mult * mad
    lower = ma - mult * mad

    close = float(closes[-1])
    if close > upper:
        band_signal = 1.0
    elif close < lower:
        band_signal = -1.0
    else:
        band_signal = 1.0 if close >= ma else -1.0

    # MAD-정규화 값(z)을 last (loop_length + 1) bars에 대해 계산
    start = n - (loop_i + 1)
    z_values: list[float] = []
    for t in range(start, n):
        ma_t = float(ema[t])
        if not _is_finite(ma_t):
            z_values.append(float("nan"))
            continue
        win = closes[t - length_i + 1 : t + 1]
        mad_t = _mean_abs_dev(win, ma_t)
        denom = mad_t if _is_finite(mad_t) and mad_t > eps_f else 1.0
        z_values.append((float(closes[t]) - ma_t) / denom)

    z_now = float(z_values[-1])
    if not _is_finite(z_now):
        return {
            "ma": ma,
            "mad": mad,
            "upper": float(upper),
            "lower": float(lower),
            "band_signal": float(band_signal),
            "momentum_score": float("nan"),
            "momentum_signal": float("nan"),
            "hybrid_score": float("nan"),
            "hybrid_signal": float("nan"),
        }

    score = 0.0
    for i in range(1, loop_i + 1):
        z_prev = float(z_values[-1 - i])
        if not _is_finite(z_prev):
            return {
                "ma": ma,
                "mad": mad,
                "upper": float(upper),
                "lower": float(lower),
                "band_signal": float(band_signal),
                "momentum_score": float("nan"),
                "momentum_signal": float("nan"),
                "hybrid_score": float("nan"),
                "hybrid_signal": float("nan"),
            }
        if z_now > z_prev:
            score += 1.0
        elif z_now < z_prev:
            score -= 1.0

    momentum_signal = float(_sign(score))
    hybrid_score = (float(band_signal) + float(momentum_signal)) / 2.0
    hybrid_signal = float(_sign(hybrid_score))

    return {
        "ma": ma,
        "mad": mad,
        "upper": float(upper),
        "lower": float(lower),
        "band_signal": float(band_signal),
        "momentum_score": float(score),
        "momentum_signal": float(momentum_signal),
        "hybrid_score": float(hybrid_score),
        "hybrid_signal": float(hybrid_signal),
    }


class MadBollingerLoopsStrategy(Strategy):
    """MAD Bollinger Bands + Loops 하이브리드 신호 기반 롱/숏 전략."""

    INDICATOR_NAME = "MAD_BOLLINGER_LOOPS"

    def __init__(
        self,
        length: int = 20,
        multiplier: float = 2.0,
        loop_length: int = 10,
        entry_pct: float | None = None,
    ) -> None:
        super().__init__()
        if length <= 1:
            raise ValueError("length must be > 1")
        if multiplier <= 0:
            raise ValueError("multiplier must be > 0")
        if loop_length <= 0:
            raise ValueError("loop_length must be > 0")

        self.length = int(length)
        self.multiplier = float(multiplier)
        self.loop_length = int(loop_length)
        self.entry_pct = float(entry_pct) if entry_pct is not None else None

        self.is_closing: bool = False  # 청산 주문 진행 중 플래그(중복 청산 방지)

        self.params = {
            "length": self.length,
            "multiplier": self.multiplier,
            "loop_length": self.loop_length,
            "entry_pct": self.entry_pct,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {
                "length": self.length,
                "multiplier": self.multiplier,
                "loop_length": self.loop_length,
            },
        }

    def initialize(self, ctx: StrategyContext) -> None:
        ctx.register_indicator(self.INDICATOR_NAME, _mad_bollinger_loops_indicator)
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # ===== 청산 플래그 리셋 =====
        if ctx.position_size == 0:
            self.is_closing = False

        # ===== 미체결 주문 가드(라이브 전용) =====
        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        # 새 봉이 확정된 시점에서만 신호 판단 (백테스트 stoploss 시뮬레이션 호환)
        if not bool(bar.get("is_new_bar", True)):
            return

        ind = ctx.get_indicator(
            self.INDICATOR_NAME,
            length=self.length,
            multiplier=self.multiplier,
            loop_length=self.loop_length,
        )
        if not isinstance(ind, dict):
            return

        band = float(ind.get("band_signal", float("nan")))
        momentum = float(ind.get("momentum_signal", float("nan")))
        if not (_is_finite(band) and _is_finite(momentum)):
            return

        bullish = band > 0 and momentum > 0
        bearish = band < 0 and momentum < 0

        # ===== 롱 포지션 청산: 양 컴포넌트가 모두 bearish =====
        if ctx.position_size > 0 and not self.is_closing:
            if bearish:
                self.is_closing = True
                ctx.close_position(reason="MAD+Loops Exit Long (band=-1 & momentum=-1)")
                return

        # ===== 숏 포지션 청산: 양 컴포넌트가 모두 bullish =====
        if ctx.position_size < 0 and not self.is_closing:
            if bullish:
                self.is_closing = True
                ctx.close_position(reason="MAD+Loops Exit Short (band=+1 & momentum=+1)")
                return

        # ===== 롱 진입: 양 컴포넌트가 모두 bullish =====
        if ctx.position_size == 0 and bullish:
            ctx.enter_long(reason="MAD+Loops Entry Long (band=+1 & momentum=+1)", entry_pct=self.entry_pct)
            return

        # ===== 숏 진입: 양 컴포넌트가 모두 bearish =====
        if ctx.position_size == 0 and bearish:
            ctx.enter_short(reason="MAD+Loops Entry Short (band=-1 & momentum=-1)", entry_pct=self.entry_pct)
            return
