from __future__ import annotations

"""
통계적 차익거래(Statistical Arbitrage) 전략 예제 – Bollinger Bands 기반 평균회귀 전략

====================================================
전략 개요
====================================================
이 전략은 "통계적 차익거래"의 가장 기본적인 형태인
평균 회귀(Mean Reversion) 가정을 사용합니다.

핵심 아이디어:
- 가격은 단기적으로 평균(이동평균)에서 멀어질 수 있지만
- 통계적으로 다시 평균으로 되돌아오려는 성질이 있다

이를 구현하기 위해 TA-Lib의 Bollinger Bands(BBANDS)를 사용합니다.

----------------------------------------------------
Bollinger Bands 구성
----------------------------------------------------
- Middle Band : n기간 단순 이동평균(SMA)
- Upper Band  : SMA + k * 표준편차
- Lower Band  : SMA - k * 표준편차

Upper / Lower Band는 일종의 "통계적 극단값" 영역으로 볼 수 있습니다.

----------------------------------------------------
매매 논리 (Long / Short)
----------------------------------------------------
1) 롱 진입 (평균 회귀 기대)
   - 가격이 Lower Band 아래에서 다시 상향 돌파할 때
   - "과매도 → 평균 복귀"를 기대하고 매수(Long)

2) 롱 청산
   - 가격이 Middle Band(평균) 이상으로 상향 돌파
   - 평균에 도달했으므로 차익 실현

3) 숏 진입
   - 가격이 Upper Band 위에서 다시 하향 돌파할 때
   - "과매수 → 평균 복귀"를 기대하고 매도(Short)

4) 숏 청산
   - 가격이 Middle Band 이하로 하향 돌파
   - 평균에 도달했으므로 차익 실현

----------------------------------------------------
시스템 연동 관점에서의 특징
----------------------------------------------------
- 주문 수량, 레버리지, 리스크 관리, StopLoss 등은
  StrategyContext(ctx)가 담당
- 이 전략은 "언제 진입/청산할지" 신호만 제공
- bar["is_new_bar"] == True 인 경우에만 신호 판단
  → 백테스트 / 라이브 환경 모두에서 일관성 유지
- 미체결 주문이 있을 경우 중복 주문 방지

----------------------------------------------------
주의 사항
----------------------------------------------------
- 단일 종목 기반 통계적 차익거래 예제
- 실제 페어 트레이딩(2종목 스프레드)보다 단순화된 형태
- 추세장이 강한 구간에서는 손실이 누적될 수 있음
"""

import importlib
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


def register_talib_indicator_all_outputs(ctx: StrategyContext, name: str) -> None:
    """TA-Lib builtin 인디케이터를 "가능하면 모든 output을 dict로" 반환하도록 오버라이드한다.

    - single-output: float
    - multi-output: dict[str, float]
    """

    try:
        import numpy as np  # type: ignore
        abstract = importlib.import_module("talib.abstract")
    except Exception:  # noqa: BLE001
        return

    def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> Any:
        output = kwargs.pop("output", None)
        output_index = kwargs.pop("output_index", None)

        if args:
            if len(args) == 1 and "period" not in kwargs and "timeperiod" not in kwargs:
                kwargs["period"] = args[0]
            else:
                raise TypeError("builtin indicator params must be passed as keywords (or single period)")

        if "period" in kwargs and "timeperiod" not in kwargs:
            kwargs["timeperiod"] = kwargs.pop("period")

        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw_inputs = inputs()
        prepared_inputs = {
            key: (np.asarray(list(values), dtype="float64") if not hasattr(values, "dtype") else values)
            for key, values in raw_inputs.items()
        }
        if "real" not in prepared_inputs and "close" in prepared_inputs:
            prepared_inputs["real"] = prepared_inputs["close"]

        normalized = name.strip().upper()
        fn = abstract.Function(normalized)
        result = fn(prepared_inputs, **kwargs)

        if isinstance(result, dict):
            out: dict[str, float] = {}
            for key, series in result.items():
                v = _last_non_nan(series)
                out[str(key)] = float(v) if v is not None else math.nan
            if output is not None:
                return float(out.get(str(output), math.nan))
            if output_index is not None:
                keys = list(out.keys())
                idx = int(output_index)
                return float(out.get(keys[idx], math.nan)) if 0 <= idx < len(keys) else math.nan
            return out

        if isinstance(result, (list, tuple)):
            series_list = list(result)
            values: list[float] = []
            for series in series_list:
                v = _last_non_nan(series)
                values.append(float(v) if v is not None else math.nan)
            names = (
                ["upperband", "middleband", "lowerband"][: len(values)]
                if normalized == "BBANDS"
                else [f"output_{i}" for i in range(len(values))]
            )
            if output is not None:
                try:
                    return values[names.index(str(output))]
                except ValueError:
                    return math.nan
            if output_index is not None:
                idx = int(output_index)
                return values[idx] if 0 <= idx < len(values) else math.nan
            return {names[i]: values[i] for i in range(len(values))}

        v = _last_non_nan(result)
        return float(v) if v is not None else math.nan

    ctx.register_indicator(name, _indicator)


def crossed_above(prev: float, current: float, level: float) -> bool:
    """prev < level <= current"""
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    """current <= level < prev"""
    return current <= level < prev


class BollingerStatArbStrategy(Strategy):
    """
    Bollinger Bands 기반 통계적 차익거래 전략

    - 평균(Middle Band)으로의 회귀를 가정
    - 극단(Lower / Upper Band)에서 반전 신호 발생 시 진입
    """

    INDICATOR_NAME = "BBANDS"

    def __init__(
        self,
        period: int = 20,
        stddev: float = 2.0,
    ) -> None:
        super().__init__()

        self.period = int(period)
        self.stddev = float(stddev)

        # 이전 봉 기준 가격과 밴드 값 저장
        self.prev_close: float | None = None
        self.prev_upper: float | None = None
        self.prev_middle: float | None = None
        self.prev_lower: float | None = None

        self.is_closing: bool = False

        # 메타 정보 (로그 / 분석용)
        self.params = {
            "period": self.period,
            "stddev": self.stddev,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {
                "period": self.period,
                "nbdevup": self.stddev,
                "nbdevdn": self.stddev,
            }
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, self.INDICATOR_NAME)

        self.prev_close = None
        self.prev_upper = None
        self.prev_middle = None
        self.prev_lower = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # 포지션이 없으면 청산 플래그 리셋
        if ctx.position_size == 0:
            self.is_closing = False

        # 미체결 주문 존재 시 아무 것도 하지 않음
        if ctx.get_open_orders():
            return

        # 확정 봉에서만 로직 수행
        if not bool(bar.get("is_new_bar", True)):
            return

        close_price = float(bar.get("close", math.nan))
        bands = ctx.get_indicator(
            self.INDICATOR_NAME,
            period=self.period,
            nbdevup=self.stddev,
            nbdevdn=self.stddev,
        )

        if not isinstance(bands, dict):
            return

        upper = float(bands.get("upperband", math.nan))
        middle = float(bands.get("middleband", math.nan))
        lower = float(bands.get("lowerband", math.nan))

        if not all(map(math.isfinite, [close_price, upper, middle, lower])):
            return

        if self.prev_close is None:
            self.prev_close = close_price
            self.prev_upper = upper
            self.prev_middle = middle
            self.prev_lower = lower
            return

        # =========================
        # 롱 포지션 청산
        # =========================
        if ctx.position_size > 0 and not self.is_closing:
            if crossed_above(self.prev_close, close_price, middle):
                self.is_closing = True
                ctx.close_position(reason="Mean Reversion Exit Long")
                self._update_prev(close_price, upper, middle, lower)
                return

        # =========================
        # 숏 포지션 청산
        # =========================
        if ctx.position_size < 0 and not self.is_closing:
            if crossed_below(self.prev_close, close_price, middle):
                self.is_closing = True
                ctx.close_position(reason="Mean Reversion Exit Short")
                self._update_prev(close_price, upper, middle, lower)
                return

        # =========================
        # 롱 진입
        # =========================
        if ctx.position_size == 0:
            if crossed_above(self.prev_close, close_price, lower):
                ctx.enter_long(reason="StatArb Long: Revert from Lower Band")

        # =========================
        # 숏 진입
        # =========================
        if ctx.position_size == 0:
            if crossed_below(self.prev_close, close_price, upper):
                ctx.enter_short(reason="StatArb Short: Revert from Upper Band")

        self._update_prev(close_price, upper, middle, lower)

    def _update_prev(self, close: float, upper: float, middle: float, lower: float) -> None:
        """이전 봉 기준 값 업데이트"""
        self.prev_close = close
        self.prev_upper = upper
        self.prev_middle = middle
        self.prev_lower = lower