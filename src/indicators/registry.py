"""지표 레지스트리.

LLM에게 제공할 지표 목록과 사용법을 관리합니다.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class IndicatorSpec:
    """지표 명세."""

    name: str
    display_name: str
    description: str
    parameters: dict[str, Any]
    output_type: str  # "single", "tuple", "dict"
    usage_example: str


class IndicatorRegistry:
    """지표 레지스트리."""

    INDICATORS: dict[str, IndicatorSpec] = {
        # ===== 추세 지표 =====
        "rsi": IndicatorSpec(
            name="rsi",
            display_name="RSI (Relative Strength Index)",
            description="과매수/과매도 판단 지표. 0-100 범위. 30 이하 과매도, 70 이상 과매수",
            parameters={"period": {"type": "int", "default": 14, "range": (2, 50), "description": "RSI 기간"}},
            output_type="single",
            usage_example='rsi = ctx.get_indicator("rsi", 14)  # 0~100',
        ),
        "sma": IndicatorSpec(
            name="sma",
            display_name="SMA (Simple Moving Average)",
            description="단순이동평균. 추세 방향 판단",
            parameters={"period": {"type": "int", "default": 20, "range": (2, 200), "description": "이동평균 기간"}},
            output_type="single",
            usage_example='sma20 = ctx.get_indicator("sma", 20)',
        ),
        "ema": IndicatorSpec(
            name="ema",
            display_name="EMA (Exponential Moving Average)",
            description="지수이동평균. 최근 가격에 더 큰 가중치",
            parameters={"period": {"type": "int", "default": 20, "range": (2, 200), "description": "이동평균 기간"}},
            output_type="single",
            usage_example='ema20 = ctx.get_indicator("ema", 20)',
        ),
        "macd": IndicatorSpec(
            name="macd",
            display_name="MACD",
            description="이동평균 수렴확산. 추세 전환 신호. MACD선이 Signal선을 상향 돌파하면 매수, 하향 돌파하면 매도",
            parameters={
                "fast": {"type": "int", "default": 12, "description": "빠른 이동평균 기간"},
                "slow": {"type": "int", "default": 26, "description": "느린 이동평균 기간"},
                "signal": {"type": "int", "default": 9, "description": "시그널 기간"},
            },
            output_type="tuple",  # (macd_line, signal_line, histogram)
            usage_example='macd, signal, hist = ctx.get_indicator("macd", 12, 26, 9)',
        ),
        # ===== 변동성 지표 =====
        "bollinger": IndicatorSpec(
            name="bollinger",
            display_name="Bollinger Bands",
            description="볼린저 밴드. 상단/하단 밴드 돌파로 과매수/과매도 판단. 가격이 상단 밴드를 돌파하면 과매수, 하단 밴드를 돌파하면 과매도",
            parameters={
                "period": {"type": "int", "default": 20, "description": "이동평균 기간"},
                "std_dev": {"type": "float", "default": 2.0, "description": "표준편차 배수"},
            },
            output_type="tuple",  # (upper, middle, lower)
            usage_example='upper, middle, lower = ctx.get_indicator("bollinger", 20, 2.0)',
        ),
        "atr": IndicatorSpec(
            name="atr",
            display_name="ATR (Average True Range)",
            description="평균진폭. 변동성 측정, 손절가 설정에 활용",
            parameters={"period": {"type": "int", "default": 14, "description": "ATR 기간"}},
            output_type="single",
            usage_example='atr = ctx.get_indicator("atr", 14)',
        ),
        # ===== 모멘텀 지표 =====
        "stochastic": IndicatorSpec(
            name="stochastic",
            display_name="Stochastic Oscillator",
            description="스토캐스틱. %K와 %D 크로스로 매매 신호. %K가 %D를 상향 돌파하면 매수, 하향 돌파하면 매도",
            parameters={
                "k_period": {"type": "int", "default": 14, "description": "%K 기간"},
                "d_period": {"type": "int", "default": 3, "description": "%D 기간"},
            },
            output_type="tuple",  # (%K, %D)
            usage_example='k, d = ctx.get_indicator("stochastic", 14, 3)',
        ),
        # ===== 거래량 지표 =====
        "obv": IndicatorSpec(
            name="obv",
            display_name="OBV (On Balance Volume)",
            description="누적 거래량. 가격과 거래량의 관계 분석",
            parameters={},
            output_type="single",
            usage_example='obv = ctx.get_indicator("obv")',
        ),
    }

    @classmethod
    def get_all_specs(cls) -> dict[str, IndicatorSpec]:
        """모든 지표 명세 반환."""
        return cls.INDICATORS

    @classmethod
    def get_spec(cls, name: str) -> IndicatorSpec | None:
        """지표 명세 조회."""
        return cls.INDICATORS.get(name)

    @classmethod
    def get_llm_context(cls) -> str:
        """LLM에게 제공할 지표 설명 문자열 생성.

        Returns:
            지표 설명 문자열
        """
        lines = ["## 사용 가능한 지표\n"]

        for name, spec in cls.INDICATORS.items():
            lines.append(f"### {spec.display_name}")
            lines.append(f"- 설명: {spec.description}")
            lines.append(f"- 파라미터:")
            for param_name, param_info in spec.parameters.items():
                param_desc = param_info.get("description", "")
                param_default = param_info.get("default", "")
                param_type = param_info.get("type", "")
                lines.append(f"  - `{param_name}` ({param_type}): {param_desc} (기본값: {param_default})")
            lines.append(f"- 출력 타입: {spec.output_type}")
            lines.append(f"- 사용법: `{spec.usage_example}`\n")

        return "\n".join(lines)
