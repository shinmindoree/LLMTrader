"""의도 분석 파서.

자연어 입력을 분석하여 전략 생성에 필요한 정보를 추출합니다.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from llm.client import LLMClient
from llm.prompts import get_intent_parser_system_prompt, get_intent_parser_user_prompt
from indicators.registry import IndicatorRegistry


class IntentType(str, Enum):
    """의도 타입."""

    VALID_STRATEGY = "VALID_STRATEGY"
    INCOMPLETE = "INCOMPLETE"
    OFF_TOPIC = "OFF_TOPIC"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"


@dataclass
class IntentResult:
    """의도 분석 결과."""

    intent_type: IntentType
    strategy_name: str | None = None
    required_indicators: list[str] = field(default_factory=list)
    entry_logic_description: str = ""
    exit_logic_description: str = ""
    extracted_indicators: list[str] = field(default_factory=list)
    symbol: str = "BTCUSDT"
    timeframe: str = "15m"
    entry_conditions: dict[str, str] = field(default_factory=dict)
    exit_conditions: dict[str, str] = field(default_factory=dict)
    risk_management: dict[str, Any] = field(default_factory=dict)
    missing_elements: list[str] = field(default_factory=list)
    user_message: str | None = None
    confidence: float = 0.0
    raw_response: str | None = None


class IntentParser:
    """의도 분석 파서."""

    # 지표명 매핑 (다양한 표현을 pandas-ta 지표명으로 변환)
    INDICATOR_MAPPING = {
        "rsi": "rsi",
        "relative strength index": "rsi",
        "macd": "macd",
        "moving average convergence divergence": "macd",
        "bollinger": "bollinger",
        "bollinger bands": "bollinger",
        "bbands": "bollinger",
        "atr": "atr",
        "average true range": "atr",
        "stochastic": "stochastic",
        "stoch": "stochastic",
        "stochastic oscillator": "stochastic",
        "obv": "obv",
        "on balance volume": "obv",
        "sma": "sma",
        "simple moving average": "sma",
        "ema": "ema",
        "exponential moving average": "ema",
    }

    # 타임프레임 패턴
    TIMEFRAME_PATTERNS = [
        (r"\b1m\b|\b1분\b|\b1 minute\b", "1m"),
        (r"\b5m\b|\b5분\b|\b5 minutes\b", "5m"),
        (r"\b15m\b|\b15분\b|\b15 minutes\b", "15m"),
        (r"\b30m\b|\b30분\b|\b30 minutes\b", "30m"),
        (r"\b1h\b|\b1시간\b|\b1 hour\b", "1h"),
        (r"\b4h\b|\b4시간\b|\b4 hours\b", "4h"),
        (r"\b1d\b|\b1일\b|\b1 day\b", "1d"),
    ]

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        """Intent Parser 초기화.

        Args:
            llm_client: LLM 클라이언트 (기본값: 새 인스턴스)
        """
        self.llm_client = llm_client or LLMClient()
        self.available_indicators = set(IndicatorRegistry.get_all_specs().keys())

    def _extract_indicators_regex(self, text: str) -> list[str]:
        """정규표현식으로 지표 추출 (백업).

        Args:
            text: 입력 텍스트

        Returns:
            추출된 지표명 리스트
        """
        text_lower = text.lower()
        found_indicators: set[str] = set()

        for keyword, indicator_name in self.INDICATOR_MAPPING.items():
            if keyword in text_lower and indicator_name in self.available_indicators:
                found_indicators.add(indicator_name)

        return sorted(list(found_indicators))

    def _extract_timeframe_regex(self, text: str) -> str:
        """정규표현식으로 타임프레임 추출 (백업).

        Args:
            text: 입력 텍스트

        Returns:
            추출된 타임프레임 (기본값: "15m")
        """
        for pattern, timeframe in self.TIMEFRAME_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return timeframe
        return "15m"

    def _parse_llm_response(self, response_text: str) -> IntentResult:
        """LLM 응답 파싱.

        Args:
            response_text: LLM 응답 텍스트

        Returns:
            IntentResult
        """
        # JSON 추출 (코드 블록 제거)
        json_text = response_text.strip()
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0].strip()
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            # JSON 파싱 실패 시 기본값 반환
            return IntentResult(
                intent_type=IntentType.INCOMPLETE,
                confidence=0.0,
                raw_response=response_text,
                missing_elements=["LLM 응답 파싱 실패"],
            )

        # IntentType 변환
        intent_type_str = data.get("intent_type", "INCOMPLETE")
        try:
            intent_type = IntentType(intent_type_str)
        except ValueError:
            intent_type = IntentType.INCOMPLETE

        # 지표명 검증 (pandas-ta 지원 지표만)
        extracted_indicators = data.get("extracted_indicators", [])
        validated_indicators = [
            ind for ind in extracted_indicators if ind in self.available_indicators
        ]
        required_indicators = data.get("required_indicators", extracted_indicators)
        validated_required_indicators = [
            ind for ind in required_indicators if ind in self.available_indicators
        ]

        # 기본값 설정
        risk_management = data.get("risk_management", {})
        if not risk_management:
            risk_management = {
                "stop_loss_pct": 0.05,
                "take_profit_pct": None,
                "max_position": 0.1,
            }
        else:
            risk_management.setdefault("stop_loss_pct", 0.05)
            risk_management.setdefault("take_profit_pct", None)
            risk_management.setdefault("max_position", 0.1)

        return IntentResult(
            intent_type=intent_type,
            strategy_name=data.get("strategy_name"),
            required_indicators=validated_required_indicators if validated_required_indicators else validated_indicators,
            entry_logic_description=data.get("entry_logic_description", ""),
            exit_logic_description=data.get("exit_logic_description", ""),
            extracted_indicators=validated_indicators,
            symbol=data.get("symbol", "BTCUSDT"),
            timeframe=data.get("timeframe", "15m"),
            entry_conditions=data.get("entry_conditions", {}),
            exit_conditions=data.get("exit_conditions", {}),
            risk_management=risk_management,
            missing_elements=data.get("missing_elements", []),
            user_message=data.get("user_message"),
            confidence=float(data.get("confidence", 0.0)),
            raw_response=response_text,
        )

    async def parse(self, user_prompt: str) -> IntentResult:
        """자연어 입력을 분석하여 의도 추출.

        Args:
            user_prompt: 사용자의 자연어 입력

        Returns:
            IntentResult
        """
        if not user_prompt or not user_prompt.strip():
            return IntentResult(
                intent_type=IntentType.OFF_TOPIC,
                confidence=0.0,
                missing_elements=["입력이 비어있습니다"],
            )

        # LLM을 사용한 의도 분석
        system_prompt = get_intent_parser_system_prompt()
        user_prompt_text = get_intent_parser_user_prompt(user_prompt)

        # LLM 호출 (시스템 프롬프트와 사용자 프롬프트 결합)
        full_prompt = f"{system_prompt}\n\n{user_prompt_text}\n\n반드시 JSON 형식으로 응답하세요."

        result = await self.llm_client.generate_strategy(full_prompt)

        if not result.success or not result.code:
            # LLM 호출 실패 시 정규표현식 기반 백업 분석
            return self._parse_fallback(user_prompt)

        # LLM 응답 파싱
        intent_result = self._parse_llm_response(result.code)

        # 정규표현식으로 보완 (지표가 누락된 경우)
        if not intent_result.extracted_indicators:
            regex_indicators = self._extract_indicators_regex(user_prompt)
            if regex_indicators:
                intent_result.extracted_indicators = regex_indicators

        # 타임프레임 보완
        if intent_result.timeframe == "15m":  # 기본값인 경우 재확인
            regex_timeframe = self._extract_timeframe_regex(user_prompt)
            if regex_timeframe != "15m":
                intent_result.timeframe = regex_timeframe

        return intent_result

    def _parse_fallback(self, user_prompt: str) -> IntentResult:
        """정규표현식 기반 백업 분석.

        Args:
            user_prompt: 사용자 입력

        Returns:
            IntentResult
        """
        text_lower = user_prompt.lower()

        # Off-topic 체크
        off_topic_keywords = ["날씨", "weather", "음식", "food", "영화", "movie"]
        if any(keyword in text_lower for keyword in off_topic_keywords):
            return IntentResult(
                intent_type=IntentType.OFF_TOPIC,
                confidence=0.0,
                missing_elements=["트레이딩 전략과 관련 없는 입력"],
            )

        # 지표 추출
        extracted_indicators = self._extract_indicators_regex(user_prompt)
        timeframe = self._extract_timeframe_regex(user_prompt)

        # 진입/청산 조건 키워드 체크
        has_entry = any(
            keyword in text_lower
            for keyword in ["진입", "매수", "buy", "long", "entry", "들어가", "사"]
        )
        has_exit = any(
            keyword in text_lower
            for keyword in ["청산", "매도", "sell", "exit", "나가", "팔"]
        )

        if not extracted_indicators:
            return IntentResult(
                intent_type=IntentType.INCOMPLETE,
                extracted_indicators=[],
                timeframe=timeframe,
                missing_elements=["사용할 지표가 명시되지 않았습니다"],
                confidence=0.3,
            )

        if not has_entry or not has_exit:
            return IntentResult(
                intent_type=IntentType.INCOMPLETE,
                extracted_indicators=extracted_indicators,
                timeframe=timeframe,
                missing_elements=["진입 조건" if not has_entry else "청산 조건"],
                confidence=0.5,
            )

        return IntentResult(
            intent_type=IntentType.VALID_STRATEGY,
            extracted_indicators=extracted_indicators,
            timeframe=timeframe,
            entry_conditions={"long": "추출 필요"},
            exit_conditions={"long": "추출 필요"},
            risk_management={"stop_loss_pct": 0.05, "take_profit_pct": None, "max_position": 0.1},
            confidence=0.6,
        )
