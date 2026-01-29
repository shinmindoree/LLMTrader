---
name: strategy-verify
description: Verifies that strategy scripts in scripts/strategies/ run correctly in both backtest and live trading. If issues are found, fixes only the strategy file(s) under inspection. Use when the user asks to check, verify, or fix strategy scripts for backtest/live compatibility.
---

# 전략 스크립트 점검 및 수정

## 적용 범위

- **점검 대상**: `scripts/strategies/` 아래 전략 파일(들). 사용자가 지정하지 않으면 해당 디렉터리 내 전략 전부를 점검한다.
- **수정 허용**: 문제가 발견된 **전략 파일만** 수정한다.
- **수정 금지**: `scripts/run_backtest.py`, `scripts/run_live_trading.py`, `src/backtest/`, `src/live/`, `src/strategy/`, `indicator_strategy_template.py` 등 **그 외 모든 코드**는 수정하지 않는다.

## 목표

검증을 통과한 전략 스크립트는 **백테스트**와 **라이브 트레이딩** 모두에서 문제 없이 동작해야 한다. 점검·수정 후에는 반드시 아래 정적 체크와 런타임 검증을 모두 통과해야 한다.

---

## 1. 정적 점검 체크리스트

전략 파일을 열고 다음을 확인한다. 하나라도 맞지 않으면 해당 전략 파일만 수정한다.

### 로더 호환

- [ ] 클래스명이 `Strategy`로 **끝난다** (예: `RsiOversoldBounceLongStrategy`). `run_backtest.py`/`run_live_trading.py`는 `name.endswith("Strategy")`로 클래스를 찾는다.
- [ ] `Strategy`를 상속한다 (`from strategy.base import Strategy`).
- [ ] `initialize(self, ctx: StrategyContext) -> None`, `on_bar(self, ctx: StrategyContext, bar: dict) -> None`가 구현되어 있다.

### 백테스트·라이브 공통 규칙 (src/strategy/AGENTS.md)

- [ ] **진입 전**: `ctx.position_size == 0`일 때만 `enter_long`/`enter_short` 호출.
- [ ] **청산 전**: 롱 청산은 `ctx.position_size > 0`, 숏 청산은 `ctx.position_size < 0` 확인 후 `close_position` 호출.
- [ ] **prev 갱신**: 크로스/이전값 갱신은 `bar.get("is_new_bar", True)`가 True일 때만 수행. (틱마다 갱신하면 오탐 발생.)

### 라이브 호환 (미체결 주문 가드)

- [ ] `on_bar` 진입부에서 **미체결 주문 가드**가 있다: `if ctx.get_open_orders(): return`.  
  (백테스트에서는 빈 리스트이지만, 라이브에서는 중복 주문 방지에 필수.)

### 봉 확정 가드

- [ ] 크로스/이전값 판단 전에 **봉 확정 가드**가 있다: `if not bar.get("is_new_bar", True): return`.  
  백테스트 stoploss 시뮬레이션 및 라이브와 동작을 맞추기 위해 필요.

### 인디케이터 사용 시

- [ ] TA-Lib builtin 사용 시 `initialize`에서 `register_talib_indicator_all_outputs(ctx, INDICATOR_NAME)` 또는 동일 역할의 등록을 한다.
- [ ] `ctx.get_indicator(...)` 반환값에 `math.isfinite(...)` 검사를 하고, non-finite면 `return` 처리한다.
- [ ] `self.params`, `self.indicator_config`가 존재한다 (runner/로그 호환).

### on_bar 실행 순서 (인디케이터 기반 전략)

아래 순서가 지켜져 있는지 확인한다. 순서가 어긋나 있으면 해당 전략 파일만 수정한다.

1. `ctx.position_size == 0`이면 `self.is_closing = False`
2. `ctx.get_open_orders()` 있으면 `return`
3. `bar.get("is_new_bar", True)`가 False면 `return`
4. 지표 조회 → `isfinite` 검사 → prev 초기화(필요 시 `return`)
5. 청산: `position_size > 0` / `< 0` 및 `not self.is_closing` 검사 후 `close_position`, `is_closing = True`, prev 갱신, `return`
6. 진입: `position_size == 0`일 때만 `enter_long`/`enter_short`
7. 마지막에 prev 값 갱신

---

## 2. 런타임 검증

정적 체크를 통과한 뒤, **반드시** 아래 두 가지를 실행해 통과시킨다.

### 2-1. 전략 로드 검증

전략 모듈이 로드되고 `*Strategy` 클래스가 인스턴스화되는지 확인한다.

프로젝트 루트에서:

```bash
uv run python -c "
import sys
from pathlib import Path
sys.path.insert(0, 'src')
from strategy.base import Strategy
import importlib.util
path = Path('scripts/strategies/대상전략.py')  # 프로젝트 루트 기준
spec = importlib.util.spec_from_file_location('custom_strategy', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
cls = next((getattr(mod, n) for n in dir(mod) if n.endswith('Strategy') and n != 'Strategy'), None)
assert cls is not None and issubclass(cls, Strategy)
inst = cls()
print('OK:', cls.__name__)
"
```

대상 전략 경로를 실제 파일명으로 바꿔 실행. 에러 없이 `OK: 클래스명`이 나와야 한다.

### 2-2. 백테스트 실행

짧은 기간으로 백테스트를 한 번 돌려 크래시/예외가 없는지 확인한다.

```bash
uv run python scripts/run_backtest.py scripts/strategies/대상전략.py \
  --symbol BTCUSDT --candle-interval 1h \
  --start-date 2024-06-01 --end-date 2024-06-03
```

- `대상전략.py`를 점검 중인 전략 파일명으로 교체.
- 종료 코드 0으로 정상 종료되고, 백테스트 결과(JSON 등)가 출력되면 통과.

### 2-3. 라이브 로드 검증 (선택)

실제 주문 없이 라이브 스크립트가 전략을 로드·빌드하는지 확인할 수 있다.  
`run_live_trading.py`가 `--yes` 없이도 로드 단계까지는 진행하는지 프로젝트 동작에 맞게 실행.  
(로드 단계에서 실패하면 전략 파일만 수정해 재검증.)

---

## 3. 점검·수정 워크플로우

1. **대상 결정**: 사용자가 지정한 전략 파일만 점검하거나, `scripts/strategies/` 내 전략 전체를 점검한다.
2. **정적 점검**: 위 체크리스트대로 각 전략 파일을 검사한다. 불통과 항목이 있으면 **해당 전략 파일만** 수정한다.
3. **로드 검증**: 2-1을 해당 전략에 대해 실행. 실패하면 전략 파일만 수정 후 다시 실행.
4. **백테스트 검증**: 2-2를 실행. 실패하면 전략 파일만 수정 후 다시 2-1, 2-2 반복.
5. **완료 조건**: 정적 체크 전부 통과 + 로드 성공 + 백테스트 정상 종료.  
   이 상태가 되면 “해당 전략 스크립트는 백테스트와 라이브 트레이딩에서 동작하도록 점검·수정되었다”고 본다.

**Golden rule**: 수정은 **전략 파일에만** 적용한다. 검증 실패 시 전략 파일을 수정한 뒤 로드·백테스트를 **반드시 다시 실행**해 통과할 때까지 반복한다.

---

## 4. 참고

- 전략 인터페이스: `src/strategy/AGENTS.md`
- 인디케이터 전략 구조/순서: `.cursor/skills/indicator-strategy/SKILL.md`
- 백테스트 실행: `scripts/run_backtest.py`
- 라이브 실행: `scripts/run_live_trading.py`, `scripts/AGENTS.md`
