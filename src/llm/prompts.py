"""LLM 프롬프트 템플릿."""

from pathlib import Path
from typing import TYPE_CHECKING

from indicators.registry import IndicatorRegistry

if TYPE_CHECKING:
    from llm.intent_parser import IntentResult


def get_context_api_spec() -> str:
    """StrategyContext API 명세 문자열 생성.

    Returns:
        Context API 명세 문자열
    """
    return """
## StrategyContext API 명세

### 필수 속성 (Properties)
| 속성 | 타입 | 설명 |
|------|------|------|
| `ctx.current_price` | float | 현재 가격 |
| `ctx.position_size` | float | 포지션 크기 (양수=롱, 음수=숏, 0=없음) |
| `ctx.position_entry_price` | float | 진입가 (포지션 없으면 0) |
| `ctx.unrealized_pnl` | float | 미실현 손익 |
| `ctx.balance` | float | 계좌 잔고 |
| `ctx.total_equity` | float | 총 자산 (balance + unrealized_pnl) |
| `ctx.leverage` | float | 레버리지 |
| `ctx.position_entry_balance` | float | 진입 시점 잔고 |

### 필수 메서드 (Methods)
| 메서드 | 설명 |
|--------|------|
| `ctx.buy(quantity, reason="")` | 롱 진입 또는 숏 청산 |
| `ctx.sell(quantity, reason="")` | 숏 진입 또는 롱 청산 |
| `ctx.close_position(reason="")` | 전체 포지션 청산 |
| `ctx.get_indicator(name, *args, **kwargs)` | 지표 조회 |
| `ctx.get_open_orders()` | 미체결 주문 목록 (list) |

### 지원 지표
{available_indicators}
"""


def get_safety_rules() -> str:
    """안전 규칙 문자열 생성.

    Returns:
        안전 규칙 문자열
    """
    return """
## 필수 안전 규칙 (반드시 지켜야 함)

### 1. 중복 주문 방지
```python
# 미체결 주문이 있으면 즉시 리턴
open_orders = getattr(ctx, "get_open_orders", lambda: [])()
if open_orders:
    return
```

### 2. 중복 청산 방지
```python
# 청산 진행 중 플래그 사용
if ctx.position_size != 0 and not self.is_closing:
    if should_close:
        self.is_closing = True
        ctx.close_position(reason="...")

# 포지션 없으면 플래그 리셋
if ctx.position_size == 0:
    self.is_closing = False
```

### 3. 포지션 상태 확인 후 주문
```python
# 롱 진입: 포지션 없을 때만
if ctx.position_size == 0:
    ctx.buy(qty, reason="Entry Long")

# 롱 청산: 롱 포지션 있을 때만
if ctx.position_size > 0:
    ctx.close_position(reason="Exit Long")

# 숏 진입: 포지션 없을 때만
if ctx.position_size == 0:
    ctx.sell(qty, reason="Entry Short")

# 숏 청산: 숏 포지션 있을 때만
if ctx.position_size < 0:
    ctx.close_position(reason="Exit Short")
```

### 4. RSI 크로스 판단은 새 봉에서만
```python
# tick에서는 StopLoss만, RSI 크로스는 새 봉에서만
if not bool(bar.get("is_new_bar", True)):
    return  # StopLoss 체크 후 리턴

rsi = float(ctx.get_indicator("rsi", self.rsi_period))

if self.prev_rsi is None:
    self.prev_rsi = rsi
    return

# 크로스 판단 로직
if self.prev_rsi < threshold <= rsi:  # 상향 돌파
    ...

self.prev_rsi = rsi  # 새 봉에서만 갱신
```

### 5. 자동 포지션 사이징 (레버리지 반영)
```python
leverage = float(getattr(ctx, "leverage", 1.0) or 1.0)
equity = float(getattr(ctx, "total_equity", 0.0) or 0.0)
price = float(getattr(ctx, "current_price", 0.0) or 0.0)

if equity > 0 and price > 0:
    target_notional = equity * leverage * max_position * 0.98
    raw_qty = target_notional / price
    
    # 수량 반올림 (거래소 step_size 규격)
    from decimal import Decimal, ROUND_DOWN
    dq = (Decimal(str(raw_qty)) / Decimal(str(qty_step))).to_integral_value(
        rounding=ROUND_DOWN
    ) * Decimal(str(qty_step))
    qty = float(dq)
    
    if qty >= min_qty:
        ctx.buy(qty, reason="...")
```

### 6. StopLoss는 진입 시점 balance 기준
```python
entry_balance = float(getattr(ctx, "position_entry_balance", 0.0) or 0.0)
unrealized_pnl = float(getattr(ctx, "unrealized_pnl", 0.0) or 0.0)

if entry_balance > 0:
    pnl_pct = unrealized_pnl / entry_balance
    if pnl_pct <= -stop_loss_pct:  # 예: -0.05 = -5%
        self.is_closing = True
        ctx.close_position(reason="StopLoss")
```
"""


def get_pandas_ta_instruction() -> str:
    """pandas-ta 사용 지침.

    Returns:
        pandas-ta 사용 지침 문자열
    """
    return """
## 중요: 지표 계산은 pandas-ta를 사용하라

**절대 직접 수식을 구현하지 마세요.** 모든 지표 계산은 pandas-ta 라이브러리를 사용해야 합니다.

### pandas-ta 사용 예시

```python
# RSI 계산
rsi = ctx.get_indicator("rsi", 14)  # pandas-ta 사용

# MACD 계산
macd, signal, hist = ctx.get_indicator("macd", 12, 26, 9)  # pandas-ta 사용

# Bollinger Bands 계산
upper, middle, lower = ctx.get_indicator("bollinger", 20, 2.0)  # pandas-ta 사용

# ATR 계산
atr = ctx.get_indicator("atr", 14)  # pandas-ta 사용

# Stochastic 계산
k, d = ctx.get_indicator("stochastic", 14, 3)  # pandas-ta 사용
```

### 금지 사항
- 직접 RSI 수식을 구현하는 것 (예: gains/losses 계산)
- 직접 이동평균을 계산하는 것 (예: sum(prices) / len(prices))
- 직접 MACD를 계산하는 것

**모든 지표는 `ctx.get_indicator()`를 통해 pandas-ta로 계산됩니다.**
"""


def get_reference_code() -> str:
    """참조 코드 (rsi_long_short_strategy.py) 읽기.

    Returns:
        참조 코드 문자열
    """
    project_root = Path(__file__).parent.parent.parent
    reference_file = project_root / "rsi_long_short_strategy.py"

    if not reference_file.exists():
        return "# 참조 코드 파일을 찾을 수 없습니다."

    try:
        return reference_file.read_text(encoding="utf-8")
    except Exception as e:
        return f"# 참조 코드 읽기 실패: {e}"


def get_system_prompt() -> str:
    """시스템 프롬프트 생성.

    Returns:
        시스템 프롬프트 문자열
    """
    available_indicators = IndicatorRegistry.get_llm_context()

    return f"""
당신은 암호화폐 트레이딩 전략 코드를 생성하는 전문가입니다.

## 기본 규칙
1. 반드시 Strategy 클래스를 상속해야 합니다
2. initialize()와 on_bar() 메서드를 구현해야 합니다
3. StrategyContext의 메서드만 사용할 수 있습니다
4. **항상 pandas-ta를 사용하여 지표를 계산하라** (직접 수식 구현 금지)

{get_context_api_spec().format(available_indicators=available_indicators)}

{get_pandas_ta_instruction()}

{get_safety_rules()}

## 참조 코드 (반드시 이 패턴을 따르세요)
```python
{get_reference_code()}
```

위 참조 코드의 패턴을 따라 안전하고 검증된 전략 코드를 생성하세요.
"""


def get_user_prompt_template() -> str:
    """사용자 프롬프트 템플릿.

    Returns:
        사용자 프롬프트 템플릿 문자열
    """
    return """
## 사용자 요청
{user_prompt}

위 요청을 기반으로 완전하고 실행 가능한 Python 전략 클래스를 생성하세요.
- 참조 코드의 패턴을 따라야 합니다
- pandas-ta를 사용하여 지표를 계산해야 합니다
- 모든 안전 규칙을 준수해야 합니다
"""


def get_intent_parser_system_prompt() -> str:
    """Intent Parser 전용 시스템 프롬프트.

    Returns:
        Intent Parser 시스템 프롬프트
    """
    available_indicators = IndicatorRegistry.get_llm_context()

    return f"""
당신은 트레이딩 전략 의도를 분석하는 전문가입니다.

## 작업
사용자의 자연어 입력을 분석하여 다음 정보를 추출하세요:

1. **의도 타입**: VALID_STRATEGY, INCOMPLETE, OFF_TOPIC, CLARIFICATION_NEEDED
2. **전략명**: 전략 이름 (예: "RSI 롱숏 전략", "터틀 트레이딩")
3. **필수 지표**: pandas-ta 지표명 리스트 (rsi, macd, bollinger, atr, stochastic, obv, sma, ema)
4. **타겟 심볼**: 거래할 암호화폐 쌍 (기본값: BTCUSDT)
5. **타임프레임**: 거래 시간대 (1m, 5m, 15m, 30m, 1h, 4h, 1d 등, 기본값: 15m)
6. **진입 로직 설명**: 자연어로 진입 조건 설명 (예: "rsi < 30 일 때 매수")
7. **청산 로직 설명**: 자연어로 청산 조건 설명 (예: "rsi > 70 일 때 매도")
8. **진입/청산 조건**: 상세 조건 (entry_conditions, exit_conditions)
9. **리스크 관리**: StopLoss, TakeProfit 등 (기본값: stop_loss_pct=0.05)
10. **누락 요소**: 전략 생성에 필요한데 입력에서 누락된 정보
11. **사용자 메시지**: 사용자에게 보여줄 피드백/제안 (선택)
12. **신뢰도**: 분석 신뢰도 (0.0~1.0)

## 추상적 전략 처리
사용자가 "터틀 트레이딩", "돈차트 전략" 등 추상적인 전략명을 언급하면, 표준 로직으로 구체화하세요:

- **터틀 트레이딩**: 20일/55일 이동평균선 돌파, ATR 기반 포지션 사이징
- **골든 크로스**: 단기 이동평균선이 장기 이동평균선을 상향 돌파
- **데이트레이딩**: 1분~15분 타임프레임, 단기 추세 추종

## 현실성 검증
불가능하거나 비현실적인 요청은 감지하고 대안을 제시하세요:

- **HFT (고빈도 거래)**: 마이크로초 단위 거래는 불가능 → "스캘핑 전략(1분~5분)으로 제안" 또는 user_message에 설명
- **과도한 레버리지**: 100배 이상은 위험 → user_message에 경고
- **불가능한 지표**: 지원하지 않는 지표 → user_message에 설명

## 사용 가능한 지표
{available_indicators}

## 응답 형식
반드시 다음 JSON 형식으로 응답하세요:

```json
{{
    "intent_type": "VALID_STRATEGY" | "INCOMPLETE" | "OFF_TOPIC" | "CLARIFICATION_NEEDED",
    "strategy_name": "RSI 롱숏 전략",
    "required_indicators": ["rsi"],
    "entry_logic_description": "rsi < 30 일 때 매수",
    "exit_logic_description": "rsi > 70 일 때 매도",
    "extracted_indicators": ["rsi"],
    "symbol": "BTCUSDT",
    "timeframe": "15m",
    "entry_conditions": {{
        "long": "RSI가 30 아래에서 30 상향 돌파",
        "short": "RSI가 70 위에서 70 하향 돌파"
    }},
    "exit_conditions": {{
        "long": "RSI가 70 상향 돌파",
        "short": "RSI가 30 하향 돌파"
    }},
    "risk_management": {{
        "stop_loss_pct": 0.05,
        "take_profit_pct": null,
        "max_position": 0.1
    }},
    "missing_elements": [],
    "user_message": null,
    "confidence": 0.9
}}
```

## 예시

### 예시 1: 유효한 전략
입력: "RSI가 30 아래에서 30을 상향 돌파하면 롱 진입, 70을 상향 돌파하면 청산"
응답:
```json
{{
    "intent_type": "VALID_STRATEGY",
    "strategy_name": "RSI 롱 전략",
    "required_indicators": ["rsi"],
    "entry_logic_description": "RSI가 30 아래에서 30 상향 돌파 시 롱 진입",
    "exit_logic_description": "RSI가 70 상향 돌파 시 청산",
    "extracted_indicators": ["rsi"],
    "symbol": "BTCUSDT",
    "timeframe": "15m",
    "entry_conditions": {{"long": "RSI가 30 아래에서 30 상향 돌파"}},
    "exit_conditions": {{"long": "RSI가 70 상향 돌파"}},
    "risk_management": {{"stop_loss_pct": 0.05, "take_profit_pct": null, "max_position": 0.1}},
    "missing_elements": [],
    "user_message": null,
    "confidence": 0.95
}}
```

### 예시 2: 추상적 전략 (터틀 트레이딩)
입력: "터틀 트레이딩 전략"
응답:
```json
{{
    "intent_type": "VALID_STRATEGY",
    "strategy_name": "터틀 트레이딩",
    "required_indicators": ["sma", "atr"],
    "entry_logic_description": "20일 이동평균선을 상향 돌파 시 롱 진입, 55일 이동평균선을 하향 돌파 시 숏 진입",
    "exit_logic_description": "20일 이동평균선을 하향 돌파 시 롱 청산, 55일 이동평균선을 상향 돌파 시 숏 청산",
    "extracted_indicators": ["sma", "atr"],
    "symbol": "BTCUSDT",
    "timeframe": "1d",
    "entry_conditions": {{"long": "20일 SMA 상향 돌파", "short": "55일 SMA 하향 돌파"}},
    "exit_conditions": {{"long": "20일 SMA 하향 돌파", "short": "55일 SMA 상향 돌파"}},
    "risk_management": {{"stop_loss_pct": 0.05, "take_profit_pct": null, "max_position": 0.1}},
    "missing_elements": [],
    "user_message": null,
    "confidence": 0.9
}}
```

### 예시 3: 불가능한 요청 (HFT)
입력: "마이크로초 단위 고빈도 거래"
응답:
```json
{{
    "intent_type": "INCOMPLETE",
    "strategy_name": null,
    "required_indicators": [],
    "entry_logic_description": "",
    "exit_logic_description": "",
    "extracted_indicators": [],
    "symbol": "BTCUSDT",
    "timeframe": "15m",
    "entry_conditions": {{}},
    "exit_conditions": {{}},
    "risk_management": {{"stop_loss_pct": 0.05, "take_profit_pct": null, "max_position": 0.1}},
    "missing_elements": ["마이크로초 단위 거래는 지원하지 않습니다"],
    "user_message": "마이크로초 단위 고빈도 거래는 불가능합니다. 대신 스캘핑 전략(1분~5분 타임프레임)을 제안합니다.",
    "confidence": 0.3
}}
```

### 예시 4: 불완전한 입력
입력: "RSI를 사용해서 매매하고 싶어"
응답:
```json
{{
    "intent_type": "INCOMPLETE",
    "strategy_name": "RSI 전략",
    "required_indicators": ["rsi"],
    "entry_logic_description": "",
    "exit_logic_description": "",
    "extracted_indicators": ["rsi"],
    "symbol": "BTCUSDT",
    "timeframe": "15m",
    "entry_conditions": {{}},
    "exit_conditions": {{}},
    "risk_management": {{"stop_loss_pct": 0.05, "take_profit_pct": null, "max_position": 0.1}},
    "missing_elements": ["진입 조건", "청산 조건"],
    "user_message": null,
    "confidence": 0.5
}}
```

### 예시 5: Off-topic
입력: "오늘 날씨가 좋네요"
응답:
```json
{{
    "intent_type": "OFF_TOPIC",
    "strategy_name": null,
    "required_indicators": [],
    "entry_logic_description": "",
    "exit_logic_description": "",
    "extracted_indicators": [],
    "symbol": "BTCUSDT",
    "timeframe": "15m",
    "entry_conditions": {{}},
    "exit_conditions": {{}},
    "risk_management": {{"stop_loss_pct": 0.05, "take_profit_pct": null, "max_position": 0.1}},
    "missing_elements": [],
    "user_message": null,
    "confidence": 0.0
}}
```
"""


def get_intent_parser_user_prompt(user_prompt: str) -> str:
    """Intent Parser 사용자 프롬프트.

    Args:
        user_prompt: 사용자의 자연어 입력

    Returns:
        Intent Parser 사용자 프롬프트
    """
    return f"""
사용자 입력을 분석하여 JSON 형식으로 응답하세요:

{user_prompt}
"""
