"""코드 생성기.

전략 명세를 Python 코드로 변환합니다.
"""

import json

from llm.client import LLMClient
from llm.prompts import get_reference_code, get_system_prompt
from llm.spec_generator import StrategySpec
from llm.templates import get_strategy_template


class CodeGenerator:
    """코드 생성기."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        """Code Generator 초기화.

        Args:
            llm_client: LLM 클라이언트 (기본값: 새 인스턴스)
        """
        self.llm_client = llm_client or LLMClient()

    def _generate_init_params(self, spec: StrategySpec) -> str:
        """__init__ 파라미터 코드 생성.

        Args:
            spec: 전략 명세

        Returns:
            __init__ 파라미터 코드 (인덴트 없이)
        """
        lines: list[str] = []

        # 지표 파라미터
        for config in spec.indicators:
            spec_obj = None
            try:
                from indicators.registry import IndicatorRegistry

                spec_obj = IndicatorRegistry.get_spec(config.name)
            except Exception:
                pass

            if spec_obj:
                for param_name, param_info in spec_obj.parameters.items():
                    default = param_info.get("default")
                    param_key = f"{config.name}_{param_name}"
                    if param_key in spec.parameters:
                        default = spec.parameters[param_key]

                    if isinstance(default, (int, float)):
                        lines.append(f"self.{param_key} = {default}")
                    elif isinstance(default, str):
                        lines.append(f'self.{param_key} = "{default}"')
                    else:
                        lines.append(f"self.{param_key} = {default}")

        # 리스크 관리 파라미터
        lines.append(f"self.leverage = {spec.leverage}")
        lines.append(f"self.max_position = {spec.risk_management.max_position}")
        lines.append(f"self.min_quantity = {spec.risk_management.min_quantity}")
        
        # 리스크 관리 추가 변수
        lines.append(f"self.daily_loss_limit = {spec.daily_loss_limit}")
        lines.append(f"self.max_consecutive_losses = {spec.max_consecutive_losses}")
        lines.append("self.current_daily_loss = 0.0")
        lines.append("self.consecutive_loss_count = 0")
        
        # StopLoss 설정
        lines.append(f'self.stop_loss_type = "{spec.stop_loss_type}"')
        lines.append(f"self.stop_loss_value = {spec.stop_loss_value}")

        # 상태 변수는 템플릿에 이미 있으므로 제외
        # lines.append("self.prev_rsi: float | None = None")
        # lines.append("self.is_closing: bool = False")

        # 템플릿에서 {{init_params}} 앞에 인덴트가 없으므로 각 줄에 8칸 인덴트 추가
        indented_lines = []
        for line in lines:
            if line.strip():
                indented_lines.append("        " + line)
            else:
                indented_lines.append("")
        
        return "\n".join(indented_lines) if indented_lines else "        pass"

    def _generate_indicator_calls(self, spec: StrategySpec) -> str:
        """지표 호출 코드 생성.

        Args:
            spec: 전략 명세

        Returns:
            지표 호출 코드 (인덴트 없이)
        """
        lines: list[str] = []

        for config in spec.indicators:
            indicator_name = config.name
            params = config.params

            # 파라미터 값 가져오기
            param_values: list[str] = []
            for param_name, param_value in params.items():
                param_key = f"{indicator_name}_{param_name}"
                if param_key in spec.parameters:
                    param_value = spec.parameters[param_key]
                param_values.append(str(param_value))

            # 지표 호출 코드 생성 (인덴트 없이)
            if indicator_name == "rsi":
                period = param_values[0] if param_values else "14"
                lines.append(f'rsi = float(ctx.get_indicator("rsi", {period}))')
                lines.append("if self.prev_rsi is None:")
                lines.append("    self.prev_rsi = rsi")
                lines.append("    return")
            elif indicator_name == "macd":
                fast = param_values[0] if len(param_values) > 0 else "12"
                slow = param_values[1] if len(param_values) > 1 else "26"
                signal = param_values[2] if len(param_values) > 2 else "9"
                lines.append(f'macd, signal, hist = ctx.get_indicator("macd", {fast}, {slow}, {signal})')
            elif indicator_name == "bollinger":
                period = param_values[0] if len(param_values) > 0 else "20"
                std_dev = param_values[1] if len(param_values) > 1 else "2.0"
                lines.append(f'upper, middle, lower = ctx.get_indicator("bollinger", {period}, {std_dev})')
            elif indicator_name == "atr":
                period = param_values[0] if param_values else "14"
                lines.append(f'atr = float(ctx.get_indicator("atr", {period}))')
            elif indicator_name == "stochastic":
                k_period = param_values[0] if len(param_values) > 0 else "14"
                d_period = param_values[1] if len(param_values) > 1 else "3"
                lines.append(f'k, d = ctx.get_indicator("stochastic", {k_period}, {d_period})')
            elif indicator_name == "obv":
                lines.append('obv = float(ctx.get_indicator("obv"))')
            elif indicator_name in ["sma", "ema"]:
                period = param_values[0] if param_values else "20"
                lines.append(f'{indicator_name}{period} = float(ctx.get_indicator("{indicator_name}", {period}))')

        # 템플릿에서 {{trading_logic}} 앞에 8칸 인덴트가 있으므로 각 줄에 8칸 인덴트 추가
        indented_lines = []
        for line in lines:
            if line.strip():
                # 각 줄에 8칸 인덴트 추가
                indented_lines.append("        " + line)
            else:
                indented_lines.append("")
        
        return "\n".join(indented_lines) if indented_lines else "        # 지표 계산"

    def _generate_risk_guard(self, spec: StrategySpec) -> str:
        """리스크 관리 가드 코드 생성.

        Args:
            spec: 전략 명세

        Returns:
            리스크 관리 가드 코드 (8칸 인덴트 포함)
        """
        lines: list[str] = []
        lines.append("# === 리스크 관리 가드 ===")
        lines.append("if self.daily_loss_limit > 0 and self.current_daily_loss >= self.daily_loss_limit:")
        lines.append("    return  # 일일 손실 한도 초과")
        lines.append("if self.max_consecutive_losses > 0 and self.consecutive_loss_count >= self.max_consecutive_losses:")
        lines.append("    return  # 최대 연속 손실 초과")
        lines.append("")
        
        indented_lines = []
        for line in lines:
            if line.strip():
                indented_lines.append("        " + line)
            else:
                indented_lines.append("")
        
        return "\n".join(indented_lines) if indented_lines else ""

    def _generate_entry_logic(self, spec: StrategySpec) -> str:
        """진입 로직 코드 생성.

        Args:
            spec: 전략 명세

        Returns:
            진입 로직 코드 (8칸 인덴트 포함)
        """
        lines: list[str] = []

        # 진입 규칙
        for rule in spec.entry_rules:
            if rule.position_type == "long":
                lines.append("# 롱 진입 조건")
                lines.append(f"# {rule.condition}")
                lines.append("if ctx.position_size == 0:")
                lines.append("    # 포지션 사이징")
                lines.append("    leverage = float(getattr(ctx, 'leverage', 1.0) or 1.0)")
                lines.append("    equity = float(getattr(ctx, 'total_equity', 0.0) or 0.0)")
                lines.append("    price = float(getattr(ctx, 'current_price', 0.0) or 0.0)")
                lines.append("    if equity > 0 and price > 0:")
                lines.append("        target_notional = equity * leverage * self.max_position * 0.98")
                lines.append("        raw_qty = target_notional / price")
                lines.append("        from decimal import Decimal, ROUND_DOWN")
                lines.append("        dq = (Decimal(str(raw_qty)) / Decimal('0.001')).to_integral_value(")
                lines.append("            rounding=ROUND_DOWN")
                lines.append("        ) * Decimal('0.001')")
                lines.append("        qty = float(dq)")
                lines.append("        if qty >= self.min_quantity:")
                lines.append('            ctx.buy(qty, reason="Entry Long")')
            elif rule.position_type == "short":
                lines.append("# 숏 진입 조건")
                lines.append(f"# {rule.condition}")
                lines.append("if ctx.position_size == 0:")
                lines.append("    # 포지션 사이징")
                lines.append("    leverage = float(getattr(ctx, 'leverage', 1.0) or 1.0)")
                lines.append("    equity = float(getattr(ctx, 'total_equity', 0.0) or 0.0)")
                lines.append("    price = float(getattr(ctx, 'current_price', 0.0) or 0.0)")
                lines.append("    if equity > 0 and price > 0:")
                lines.append("        target_notional = equity * leverage * self.max_position * 0.98")
                lines.append("        raw_qty = target_notional / price")
                lines.append("        from decimal import Decimal, ROUND_DOWN")
                lines.append("        dq = (Decimal(str(raw_qty)) / Decimal('0.001')).to_integral_value(")
                lines.append("            rounding=ROUND_DOWN")
                lines.append("        ) * Decimal('0.001')")
                lines.append("        qty = float(dq)")
                lines.append("        if qty >= self.min_quantity:")
                lines.append('            ctx.sell(qty, reason="Entry Short")')

        # 템플릿에서 {{ ENTRY_LOGIC }} 앞에 8칸 인덴트가 있으므로 각 줄에 8칸 인덴트 추가
        indented_lines = []
        for line in lines:
            if line.strip():
                indented_lines.append("        " + line)
            else:
                indented_lines.append("")
        
        return "\n".join(indented_lines) if indented_lines else "        # 진입 로직"

    def _generate_exit_logic(self, spec: StrategySpec) -> str:
        """청산 로직 코드 생성.

        Args:
            spec: 전략 명세

        Returns:
            청산 로직 코드 (8칸 인덴트 포함)
        """
        lines: list[str] = []

        # 청산 규칙
        for rule in spec.exit_rules:
            if rule.position_type == "long":
                lines.append("# 롱 청산 조건")
                lines.append(f"# {rule.condition}")
                lines.append("if ctx.position_size > 0 and not self.is_closing:")
                lines.append("    # 손실 추적")
                lines.append("    unrealized_pnl = float(getattr(ctx, 'unrealized_pnl', 0.0) or 0.0)")
                lines.append("    if unrealized_pnl < 0:")
                lines.append("        self.consecutive_loss_count += 1")
                lines.append("        self.current_daily_loss -= unrealized_pnl")
                lines.append("    else:")
                lines.append("        self.consecutive_loss_count = 0  # 수익 시 리셋")
                lines.append("    self.is_closing = True")
                lines.append('    ctx.close_position(reason="Exit Long")')
            elif rule.position_type == "short":
                lines.append("# 숏 청산 조건")
                lines.append(f"# {rule.condition}")
                lines.append("if ctx.position_size < 0 and not self.is_closing:")
                lines.append("    # 손실 추적")
                lines.append("    unrealized_pnl = float(getattr(ctx, 'unrealized_pnl', 0.0) or 0.0)")
                lines.append("    if unrealized_pnl < 0:")
                lines.append("        self.consecutive_loss_count += 1")
                lines.append("        self.current_daily_loss -= unrealized_pnl")
                lines.append("    else:")
                lines.append("        self.consecutive_loss_count = 0  # 수익 시 리셋")
                lines.append("    self.is_closing = True")
                lines.append('    ctx.close_position(reason="Exit Short")')

        # 템플릿에서 {{ EXIT_LOGIC }} 앞에 8칸 인덴트가 있으므로 각 줄에 8칸 인덴트 추가
        indented_lines = []
        for line in lines:
            if line.strip():
                indented_lines.append("        " + line)
            else:
                indented_lines.append("")
        
        return "\n".join(indented_lines) if indented_lines else "        # 청산 로직"

    def _generate_stop_loss_logic(self, spec: StrategySpec) -> str:
        """StopLoss 로직 코드 생성.

        Args:
            spec: 전략 명세

        Returns:
            StopLoss 로직 코드
        """
        return """        # StopLoss 체크
        if ctx.position_size != 0 and not self.is_closing:
            entry_balance = float(getattr(ctx, "position_entry_balance", 0.0) or 0.0)
            unrealized_pnl = float(getattr(ctx, "unrealized_pnl", 0.0) or 0.0)
            
            should_close = False
            sl_reason = ""
            
            # 1. 비율(%) 기준 손절
            if self.stop_loss_type == "pct" and entry_balance > 0:
                pnl_pct = unrealized_pnl / entry_balance
                if pnl_pct <= -self.stop_loss_value:
                    should_close = True
                    sl_reason = f"StopLoss (Pct: {pnl_pct*100:.2f}%)"
            
            # 2. 금액(USDT) 기준 손절
            elif self.stop_loss_type == "amount":
                if unrealized_pnl <= -self.stop_loss_value:
                    should_close = True
                    sl_reason = f"StopLoss (Amount: {unrealized_pnl:.2f})"
            
            if should_close:
                self.is_closing = True
                self.consecutive_loss_count += 1
                self.current_daily_loss -= unrealized_pnl
                ctx.close_position(reason=sl_reason)"""

    def _generate_prev_indicator_update(self, spec: StrategySpec) -> str:
        """이전 지표 값 갱신 코드 생성.

        Args:
            spec: 전략 명세

        Returns:
            이전 지표 값 갱신 코드 (8칸 인덴트 포함)
        """
        lines: list[str] = []

        # RSI가 있으면 prev_rsi 갱신
        has_rsi = any(config.name == "rsi" for config in spec.indicators)
        if has_rsi:
            lines.append("# prev_rsi 갱신")
            lines.append("self.prev_rsi = rsi")

        # 템플릿에서 {{prev_indicator_update}} 앞에 8칸 인덴트가 있으므로 각 줄에 8칸 인덴트 추가
        indented_lines = []
        for line in lines:
            if line.strip():
                indented_lines.append("        " + line)
            else:
                indented_lines.append("")
        
        return "\n".join(indented_lines) if indented_lines else "        # 이전 지표 값 갱신"

    def _generate_code_from_spec(self, spec: StrategySpec) -> str:
        """명세로부터 코드 생성 (템플릿 기반).

        Args:
            spec: 전략 명세

        Returns:
            생성된 코드
        """
        # 템플릿 로드
        template = get_strategy_template(spec.class_name)

        # 각 부분 생성
        init_params = self._generate_init_params(spec)
        stop_loss_logic = self._generate_stop_loss_logic(spec)
        risk_guard = self._generate_risk_guard(spec)
        indicator_calls = self._generate_indicator_calls(spec)
        entry_logic = self._generate_entry_logic(spec)
        exit_logic = self._generate_exit_logic(spec)
        prev_indicator_update = self._generate_prev_indicator_update(spec)

        # 템플릿 포맷팅
        # init_params는 이미 8칸 인덴트가 포함되어 있음 (_generate_init_params에서 추가)
        code = template.format(
            init_params=init_params,
            stop_loss_logic=stop_loss_logic,
            risk_guard=risk_guard,
            INDICATOR_CALLS=indicator_calls,
            ENTRY_LOGIC=entry_logic,
            EXIT_LOGIC=exit_logic,
            prev_indicator_update=prev_indicator_update,
        )

        return code

    async def generate(self, spec: StrategySpec) -> str:
        """전략 명세를 Python 코드로 변환.

        Args:
            spec: 전략 명세

        Returns:
            생성된 Python 코드
        """
        # 먼저 템플릿 기반으로 기본 코드 생성
        base_code = self._generate_code_from_spec(spec)

        # LLM을 사용하여 코드 개선 (선택적)
        # 명세를 JSON으로 변환하여 프롬프트에 포함
        spec_dict = {
            "class_name": spec.class_name,
            "symbol": spec.symbol,
            "timeframe": spec.timeframe,
            "indicators": [
                {"name": config.name, "params": config.params} for config in spec.indicators
            ],
            "entry_rules": [
                {
                    "condition": rule.condition,
                    "action": rule.action,
                    "position_type": rule.position_type,
                }
                for rule in spec.entry_rules
            ],
            "exit_rules": [
                {
                    "condition": rule.condition,
                    "action": rule.action,
                    "position_type": rule.position_type,
                }
                for rule in spec.exit_rules
            ],
            "risk_management": {
                "stop_loss_pct": spec.risk_management.stop_loss_pct,
                "take_profit_pct": spec.risk_management.take_profit_pct,
                "max_position": spec.risk_management.max_position,
            },
        }

        # LLM 프롬프트 생성
        system_prompt = get_system_prompt()
        user_prompt = f"""
다음 전략 명세를 기반으로 완전하고 실행 가능한 Python 전략 코드를 생성하세요:

## 전략 명세
```json
{json.dumps(spec_dict, indent=2, ensure_ascii=False)}
```

## 참조 코드
```python
{get_reference_code()}
```

위 명세와 참조 코드를 기반으로 전략 코드를 생성하세요. 반드시 다음을 준수해야 합니다:
1. Strategy 클래스를 상속
2. initialize()와 on_bar() 메서드 구현
3. pandas-ta를 사용하여 지표 계산
4. 모든 안전 규칙 준수
5. StopLoss 로직 포함
6. 포지션 사이징 로직 포함
"""

        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        # LLM 호출 (선택적 - 현재는 템플릿 기반 코드 사용)
        # result = await self.llm_client.generate_strategy(full_prompt)
        # if result.success and result.code:
        #     # LLM이 생성한 코드 반환
        #     return result.code

        # 템플릿 기반 코드 반환 (안정성 우선)
        return base_code
