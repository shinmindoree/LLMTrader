---
name: indicator-strategy
description: Generates a new trading strategy file from indicator_strategy_template.py. Creates only one new file under scripts/strategies/; does not modify template, run_live_trading, or any other code. Use when the user asks to add a strategy, create an indicator-based strategy, or generate a strategy from the template.
---

# 인디케이터 기반 전략 생성

## 적용 범위

- **생성**: `scripts/strategies/` 아래 **새 전략 파일 하나만** 생성한다.
- **금지**: `indicator_strategy_template.py`, `scripts/run_live_trading.py`, 기타 모든 파일은 수정하지 않는다.

## 소스 기준

프로젝트 루트의 `indicator_strategy_template.py`를 **참조용**으로 읽고, 그 구조(헬퍼 함수 + 클래스 골격)를 그대로 따라 **새 파일**로 작성한다. 템플릿 파일 자체는 변경하지 않는다.

## 파일/클래스 명명

| 항목 | 규칙 | 예시 |
|------|------|------|
| 파일 경로 | `scripts/strategies/{이름}_strategy.py` | `scripts/strategies/rsi_oversold_bounce_long_strategy.py` |
| 파일명 | snake_case, `_strategy.py`로 끝남 | `macd_crossover_long_strategy.py` |
| 클래스명 | `*Strategy` (loader가 `name.endswith("Strategy")`로 탐색) | `RsiOversoldBounceLongStrategy` |

## 필수 구조 (백테스트·라이브 공통)

1. **임포트**: `from strategy.base import Strategy`, `from strategy.context import StrategyContext` (프로젝트 패키지 기준).
2. **헬퍼**: `_last_non_nan`, `register_talib_indicator_all_outputs`(및 필요 시 `crossed_above`, `crossed_below`)는 **템플릿 파일에서 복사**해 새 전략 파일 안에만 넣는다. 템플릿 파일(`indicator_strategy_template.py`)은 수정하지 않는다.
3. **클래스**:
   - `Strategy` 상속.
   - `__init__`: period/레벨 등 파라미터, `self.params`(dict), `self.indicator_config`(dict), 상태용 `prev_*`, `is_closing` 등.
   - `initialize(ctx)`: `register_talib_indicator_all_outputs(ctx, INDICATOR_NAME)` 호출, `prev_*`/`is_closing` 초기화.
   - `on_bar(ctx, bar)`: 아래 순서를 지킨다.

## on_bar 실행 순서 (준수 필수)

백테스트·라이브 모두에서 동일하게 동작하려면 다음 순서를 유지한다.

1. **청산 플래그 리셋**: `if ctx.position_size == 0: self.is_closing = False`
2. **미체결 주문 가드**: `if ctx.get_open_orders(): return`
3. **봉 확정 가드**: `if not bar.get("is_new_bar", True): return`  
   → 크로스/prev 갱신은 **새 봉이 올 때만** 수행.
4. **지표 조회**: `value = ctx.get_indicator(INDICATOR_NAME, period=...)` (또는 multi-output이면 해당 키 사용). `math.isfinite(value)` 검사 후 아니면 `return`.
5. **prev 초기화**: `prev_value`가 None 또는 non-finite면 `self.prev_value = value; return`
6. **청산 로직**:  
   - 롱 청산: `ctx.position_size > 0 and not self.is_closing` 일 때 조건 만족 시 `self.is_closing = True`, `ctx.close_position(...)`, `self.prev_value = value`, `return`.  
   - 숏 청산: `ctx.position_size < 0 and not self.is_closing` 일 때 동일 패턴.
7. **진입 로직**: `ctx.position_size == 0` 일 때만 `ctx.enter_long(...)` 또는 `ctx.enter_short(...)` 호출.
8. **prev 갱신**: 마지막에 `self.prev_value = value` (또는 다중 지표면 해당 prev들 갱신).

## TA-Lib builtin / custom

- **builtin**: `INDICATOR_NAME`에 TA-Lib 함수명(예: `"RSI"`, `"MACD"`) 문자열 사용. `ctx.get_indicator(name, period=...)` 등으로 호출. `indicator_config`에 `{ "RSI": {"period": 14} }` 형태로 설정.
- **custom**: `initialize()` 안에서 `ctx.register_indicator(name, func)`로 등록하고, 동일한 `name`으로 `get_indicator` 호출.

## 검증 체크리스트

생성 후 다음만 확인하면 된다.

- [ ] 새 파일 **단일** 생성, 다른 파일 수정 없음.
- [ ] 클래스명이 `*Strategy`로 끝남.
- [ ] `initialize`에서 지표 등록 및 상태 초기화.
- [ ] `on_bar`에 `get_open_orders()` 가드, `is_new_bar` 가드, `position_size`/`is_closing` 검사 후 진입·청산.
- [ ] `self.params`, `self.indicator_config` 존재 (runner/로그 호환).

## 참고

- 전략 인터페이스: `src/strategy/AGENTS.md`
- 템플릿 전체 코드: 프로젝트 루트 `indicator_strategy_template.py`
- 기존 전략 예시: `scripts/strategies/rsi_oversold_bounce_long_strategy.py`, `scripts/strategies/ema_crossover_long_strategy.py`
