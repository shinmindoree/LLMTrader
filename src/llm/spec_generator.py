"""전략 명세 생성기.

Intent 결과를 구조화된 전략 명세로 변환합니다.
"""

from dataclasses import dataclass, field
from typing import Any

from indicators.registry import IndicatorRegistry

from llm.intent_parser import IntentResult, IntentType


@dataclass
class IndicatorConfig:
    """지표 설정."""

    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Rule:
    """규칙."""

    condition: str
    action: str  # "entry" | "exit"
    position_type: str  # "long" | "short"


@dataclass
class RiskConfig:
    """리스크 관리 설정."""

    stop_loss_pct: float = 0.05
    take_profit_pct: float | None = None
    max_position: float = 0.1
    min_quantity: float = 0.001


@dataclass
class StrategySpec:
    """전략 명세."""

    class_name: str
    symbol: str
    timeframe: str
    indicators: list[IndicatorConfig] = field(default_factory=list)
    entry_rules: list[Rule] = field(default_factory=list)
    exit_rules: list[Rule] = field(default_factory=list)
    risk_management: RiskConfig = field(default_factory=RiskConfig)
    parameters: dict[str, Any] = field(default_factory=dict)
    # UI에서 입력받은 환경/리스크 설정 필드
    leverage: float = 1.0
    daily_loss_limit: float = 0.0
    max_consecutive_losses: int = 0
    stop_loss_type: str = "pct"  # "pct" | "amount"
    stop_loss_value: float = 0.05


class SpecGenerator:
    """명세 생성기."""

    def __init__(self) -> None:
        """Spec Generator 초기화."""
        self.indicator_registry = IndicatorRegistry()

    def _generate_class_name(self, intent_result: IntentResult) -> str:
        """클래스 이름 생성.

        Args:
            intent_result: Intent 결과

        Returns:
            클래스 이름
        """
        if not intent_result.extracted_indicators:
            return "GeneratedStrategy"

        # 지표명을 조합하여 클래스 이름 생성
        indicator_names = [ind.capitalize() for ind in intent_result.extracted_indicators]
        class_name = "".join(indicator_names) + "Strategy"

        # 너무 길면 첫 번째 지표만 사용
        if len(class_name) > 30:
            class_name = indicator_names[0] + "Strategy"

        return class_name

    def _generate_indicator_configs(self, intent_result: IntentResult) -> list[IndicatorConfig]:
        """지표 설정 생성.

        Args:
            intent_result: Intent 결과

        Returns:
            IndicatorConfig 리스트
        """
        configs: list[IndicatorConfig] = []

        for indicator_name in intent_result.extracted_indicators:
            spec = self.indicator_registry.get_spec(indicator_name)
            if not spec:
                continue

            # 기본 파라미터 설정
            params: dict[str, Any] = {}
            for param_name, param_info in spec.parameters.items():
                default_value = param_info.get("default")
                if default_value is not None:
                    params[param_name] = default_value

            configs.append(IndicatorConfig(name=indicator_name, params=params))

        return configs

    def _generate_entry_rules(self, intent_result: IntentResult) -> list[Rule]:
        """진입 규칙 생성.

        Args:
            intent_result: Intent 결과

        Returns:
            Rule 리스트
        """
        rules: list[Rule] = []

        entry_conditions = intent_result.entry_conditions

        # 롱 진입 규칙
        if "long" in entry_conditions:
            condition = entry_conditions["long"]
            if condition and condition.strip():
                rules.append(
                    Rule(condition=condition, action="entry", position_type="long")
                )

        # 숏 진입 규칙
        if "short" in entry_conditions:
            condition = entry_conditions["short"]
            if condition and condition.strip():
                rules.append(
                    Rule(condition=condition, action="entry", position_type="short")
                )

        return rules

    def _generate_exit_rules(self, intent_result: IntentResult) -> list[Rule]:
        """청산 규칙 생성.

        Args:
            intent_result: Intent 결과

        Returns:
            Rule 리스트
        """
        rules: list[Rule] = []

        exit_conditions = intent_result.exit_conditions

        # 롱 청산 규칙
        if "long" in exit_conditions:
            condition = exit_conditions["long"]
            if condition and condition.strip():
                rules.append(
                    Rule(condition=condition, action="exit", position_type="long")
                )

        # 숏 청산 규칙
        if "short" in exit_conditions:
            condition = exit_conditions["short"]
            if condition and condition.strip():
                rules.append(
                    Rule(condition=condition, action="exit", position_type="short")
                )

        return rules

    def _generate_risk_config(self, intent_result: IntentResult) -> RiskConfig:
        """리스크 관리 설정 생성.

        Args:
            intent_result: Intent 결과

        Returns:
            RiskConfig
        """
        risk_mgmt = intent_result.risk_management

        return RiskConfig(
            stop_loss_pct=float(risk_mgmt.get("stop_loss_pct", 0.05)),
            take_profit_pct=risk_mgmt.get("take_profit_pct"),
            max_position=float(risk_mgmt.get("max_position", 0.1)),
            min_quantity=float(risk_mgmt.get("min_quantity", 0.001)),
        )

    def _generate_parameters(self, intent_result: IntentResult, indicator_configs: list[IndicatorConfig]) -> dict[str, Any]:
        """전략 파라미터 생성.

        Args:
            intent_result: Intent 결과
            indicator_configs: 지표 설정 리스트

        Returns:
            파라미터 딕셔너리
        """
        params: dict[str, Any] = {}

        # 지표 파라미터 추가
        for config in indicator_configs:
            spec = self.indicator_registry.get_spec(config.name)
            if not spec:
                continue

            for param_name, param_value in config.params.items():
                # 파라미터 이름 생성 (지표명_파라미터명)
                param_key = f"{config.name}_{param_name}"
                params[param_key] = param_value

        # 리스크 관리 파라미터
        risk_config = self._generate_risk_config(intent_result)
        params["stop_loss_pct"] = risk_config.stop_loss_pct
        params["max_position"] = risk_config.max_position
        params["min_quantity"] = risk_config.min_quantity

        return params

    def generate(
        self, 
        intent_result: IntentResult, 
        manual_config: dict[str, Any] | None = None
    ) -> StrategySpec:
        """Intent 결과를 전략 명세로 변환.

        Args:
            intent_result: Intent 분석 결과
            manual_config: UI에서 입력한 정형 데이터 설정값

        Returns:
            StrategySpec
        """
        # 1. 기본 생성 (기존 로직 수행)
        class_name = self._generate_class_name(intent_result)
        indicator_configs = self._generate_indicator_configs(intent_result)
        entry_rules = self._generate_entry_rules(intent_result)
        exit_rules = self._generate_exit_rules(intent_result)
        risk_config = self._generate_risk_config(intent_result)
        parameters = self._generate_parameters(intent_result, indicator_configs)

        # 2. Manual Config 오버라이딩 (UI 입력값 우선 적용)
        leverage = 1.0
        daily_loss_limit = 0.0
        max_consecutive_losses = 0
        sl_type = "pct"
        sl_value = risk_config.stop_loss_pct

        if manual_config:
            # risk_config 객체 업데이트
            risk_config.max_position = manual_config.get("max_position", risk_config.max_position)
            
            # 개별 변수 추출
            leverage = manual_config.get("leverage", 1.0)
            daily_loss_limit = manual_config.get("daily_loss_limit", 0.0)
            max_consecutive_losses = manual_config.get("max_consecutive_losses", 0)
            sl_type = manual_config.get("stop_loss_type", "pct")
            sl_value = manual_config.get("stop_loss_value", 0.05)
            
            # 템플릿용 parameters 딕셔너리에도 업데이트
            parameters["leverage"] = leverage
            parameters["max_position"] = risk_config.max_position

        return StrategySpec(
            class_name=class_name,
            symbol=intent_result.symbol,
            timeframe=intent_result.timeframe,
            indicators=indicator_configs,
            entry_rules=entry_rules,
            exit_rules=exit_rules,
            risk_management=risk_config,
            parameters=parameters,
            # 신규 필드 할당
            leverage=leverage,
            daily_loss_limit=daily_loss_limit,
            max_consecutive_losses=max_consecutive_losses,
            stop_loss_type=sl_type,
            stop_loss_value=sl_value
        )
