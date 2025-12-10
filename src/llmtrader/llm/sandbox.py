"""샌드박스 환경에서 전략 코드 스모크 테스트."""

import sys
from io import StringIO
from typing import Any


class SandboxContext:
    """샌드박스용 mock 컨텍스트."""

    def __init__(self) -> None:
        """초기화."""
        self.current_price = 50000.0
        self.position_size = 0.0
        self.unrealized_pnl = 0.0
        self.balance = 10000.0
        self._sma_values = {10: 49500.0, 20: 49800.0, 30: 50100.0}

    def buy(self, quantity: float, price: float | None = None) -> None:
        """매수 (스모크 테스트용)."""
        pass

    def sell(self, quantity: float, price: float | None = None) -> None:
        """매도 (스모크 테스트용)."""
        pass

    def close_position(self) -> None:
        """청산 (스모크 테스트용)."""
        pass

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """지표 조회 (스모크 테스트용)."""
        if name == "sma":
            period = args[0] if args else kwargs.get("period", 20)
            return self._sma_values.get(period, self.current_price)
        return 0.0


class SandboxRunner:
    """샌드박스 실행기."""

    def __init__(self, timeout: int = 5) -> None:
        """실행기 초기화.

        Args:
            timeout: 타임아웃 (초)
        """
        self.timeout = timeout

    def smoke_test(self, code: str) -> tuple[bool, str]:
        """스모크 테스트 실행.

        Args:
            code: 전략 코드

        Returns:
            (성공 여부, 오류 메시지 또는 출력)
        """
        # stdout/stderr 캡처
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = StringIO()
        sys.stderr = StringIO()

        try:
            # Strategy 기본 클래스 import
            from llmtrader.strategy.base import Strategy
            from llmtrader.strategy.context import StrategyContext

            # 전역 네임스페이스 준비
            # 전체 builtins 사용하되 위험한 함수만 제거
            safe_builtins = dict(__builtins__)  # type: ignore
            # 위험한 함수 제거
            for dangerous in ["eval", "exec", "compile", "__import__", "open", "input"]:
                safe_builtins.pop(dangerous, None)
            
            namespace: dict[str, Any] = {
                "__builtins__": safe_builtins,
                # 필수 클래스 사전 주입
                "Strategy": Strategy,
                "StrategyContext": StrategyContext,
                "Any": Any,
            }

            # 코드 실행 (import 문 제거 버전)
            # import 문을 주석 처리한 버전으로 실행
            code_lines = code.split("\n")
            filtered_lines = []
            for line in code_lines:
                # import 문은 스킵 (이미 namespace에 주입됨)
                if line.strip().startswith(("from llmtrader", "import llmtrader")):
                    continue
                if line.strip().startswith("from typing import"):
                    continue
                filtered_lines.append(line)
            
            filtered_code = "\n".join(filtered_lines)
            exec(filtered_code, namespace)  # noqa: S102

            # Strategy 클래스 찾기
            strategy_class = None
            for name, obj in namespace.items():
                if (
                    isinstance(obj, type)
                    and name.endswith("Strategy")
                    and name != "Strategy"
                ):
                    strategy_class = obj
                    break

            if not strategy_class:
                return False, "No Strategy class found in generated code"

            # 인스턴스 생성 시도
            try:
                strategy = strategy_class()
            except TypeError as e:
                return False, f"Strategy __init__ error: {e}"

            # 메서드 존재 확인
            if not hasattr(strategy, "initialize"):
                return False, "Strategy missing initialize() method"
            if not hasattr(strategy, "on_bar"):
                return False, "Strategy missing on_bar() method"

            # Mock 컨텍스트로 메서드 호출 시도
            ctx = SandboxContext()
            bar = {
                "timestamp": 1700000000000,
                "open": 50000.0,
                "high": 50100.0,
                "low": 49900.0,
                "close": 50050.0,
                "volume": 100.0,
            }

            strategy.initialize(ctx)
            strategy.on_bar(ctx, bar)

            # 성공
            captured_out = sys.stdout.getvalue()
            return True, captured_out or "Smoke test passed"

        except Exception as e:  # noqa: BLE001
            captured_err = sys.stderr.getvalue()
            return False, f"Execution error: {e}\n{captured_err}"

        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

