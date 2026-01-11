"""전략 코드 검증 시스템.

3단계 검증을 통해 생성된 전략 코드의 안전성을 보장합니다.
- Level 1: 정적 검증 (문법, 금지된 import)
- Level 2: 구조 검증 (Strategy 상속, 필수 메서드)
- Level 3: 런타임 스모크 테스트 (실제 실행)
"""

import ast
import importlib.util
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from llm.utils import load_and_resample_data
from strategy.context import StrategyContext


@dataclass
class ValidationResult:
    """검증 결과."""

    is_valid: bool
    level: str  # "static", "structure", "runtime"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


# 금지된 import 목록
FORBIDDEN_IMPORTS = {
    # 시스템 접근 (위험한 것만)
    "os",
    "subprocess",
    "shutil",
    # 코드 실행
    "eval",
    "exec",
    "__import__",
    "compile",
    # 파일 시스템 (open은 함수 호출로 검사)
    # "open",  # 함수 호출로 검사
    # "file",  # Python 2 전용
    # 사용자 입력
    "input",
    "raw_input",
    # 네트워크
    "socket",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
    # 기타 위험한 모듈
    "pickle",
    "marshal",
    "ctypes",
}

# sys와 pathlib는 경로 설정을 위해 허용하되, 위험한 사용만 검사
ALLOWED_BUT_RESTRICTED = {
    "sys": ["path"],  # sys.path만 허용
    "pathlib": ["Path"],  # Path만 허용
}

# 금지된 함수 호출
FORBIDDEN_FUNCTIONS = {
    "eval",
    "exec",
    "__import__",
    "compile",
    "open",
    "file",
    "input",
    "raw_input",
}


class MockContext:
    """검증용 Mock Context.

    StrategyContext Protocol을 구현하지만 실제 주문은 실행하지 않습니다.
    """

    def __init__(self) -> None:
        """Mock Context 초기화."""
        self._current_price: float = 100000.0
        self._position_size: float = 0.0
        self._position_entry_price: float = 0.0
        self._balance: float = 10000.0
        self._leverage: float = 1.0
        self._price_history: list[float] = [100000.0] * 100
        self._closes: list[float] = [100000.0] * 100

    @property
    def current_price(self) -> float:
        """현재 가격."""
        return self._current_price

    @property
    def position_size(self) -> float:
        """현재 포지션 크기."""
        return self._position_size

    @property
    def position_entry_price(self) -> float:
        """진입가."""
        return self._position_entry_price

    @property
    def unrealized_pnl(self) -> float:
        """미실현 손익."""
        if self._position_size == 0:
            return 0.0
        return (self._current_price - self._position_entry_price) * self._position_size

    @property
    def balance(self) -> float:
        """계좌 잔고."""
        return self._balance

    @property
    def total_equity(self) -> float:
        """총 자산."""
        return self.balance + self.unrealized_pnl

    @property
    def leverage(self) -> float:
        """레버리지."""
        return self._leverage

    @property
    def position_entry_balance(self) -> float:
        """진입 시점 잔고."""
        return 10000.0 if self._position_size != 0 else 0.0

    def buy(self, quantity: float, price: float | None = None, reason: str = "") -> None:
        """매수 주문 (Mock - 실제 실행 안 함)."""
        if price is None:
            price = self._current_price
        # Mock: 포지션 업데이트만 (실제 주문 안 함)
        if self._position_size == 0:
            self._position_size = quantity
            self._position_entry_price = price
        elif self._position_size < 0:
            # 숏 포지션 청산
            self._position_size = 0.0
            self._position_entry_price = 0.0
        else:
            # 롱 포지션 추가
            total_value = self._position_size * self._position_entry_price + quantity * price
            self._position_size += quantity
            self._position_entry_price = total_value / self._position_size

    def sell(self, quantity: float, price: float | None = None, reason: str = "") -> None:
        """매도 주문 (Mock - 실제 실행 안 함)."""
        if price is None:
            price = self._current_price
        # Mock: 포지션 업데이트만 (실제 주문 안 함)
        if self._position_size == 0:
            self._position_size = -quantity
            self._position_entry_price = price
        elif self._position_size > 0:
            # 롱 포지션 청산
            self._position_size = 0.0
            self._position_entry_price = 0.0
        else:
            # 숏 포지션 추가
            total_value = abs(self._position_size) * self._position_entry_price + quantity * price
            self._position_size -= quantity
            self._position_entry_price = total_value / abs(self._position_size)

    def close_position(self, reason: str = "", use_chase: bool = False) -> None:
        """포지션 청산 (Mock - 실제 실행 안 함)."""
        self._position_size = 0.0
        self._position_entry_price = 0.0

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """지표 조회 (Mock - 기본값 반환)."""
        # 기본값 반환 (실제 계산 불필요)
        if name == "rsi":
            return 50.0
        elif name == "sma":
            return self._current_price
        elif name == "ema":
            return self._current_price
        elif name == "macd":
            return (0.0, 0.0, 0.0)
        elif name == "bollinger" or name == "bbands":
            return (self._current_price * 1.02, self._current_price, self._current_price * 0.98)
        elif name == "atr":
            return 1000.0
        elif name == "stochastic" or name == "stoch":
            return (50.0, 50.0)
        elif name == "obv":
            return 0.0
        return 0.0

    def get_open_orders(self) -> list[dict[str, Any]]:
        """미체결 주문 목록 (Mock - 항상 빈 리스트)."""
        return []


def validate_static(code: str) -> ValidationResult:
    """Level 1: 정적 검증 (문법, 금지된 import).

    Args:
        code: 검증할 코드 문자열

    Returns:
        ValidationResult
    """
    result = ValidationResult(is_valid=True, level="static")

    # 1. 문법 체크
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        result.is_valid = False
        result.errors.append(f"문법 오류: {e.msg} (라인 {e.lineno})")
        return result

    # 2. 금지된 import 검사
    for node in ast.walk(tree):
        # import 모듈명
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split(".")[0]  # 첫 번째 부분만 확인
                if module_name in FORBIDDEN_IMPORTS:
                    result.is_valid = False
                    result.errors.append(f"금지된 import: {alias.name}")

        # from 모듈 import
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_name = node.module.split(".")[0]  # 첫 번째 부분만 확인
                if module_name in FORBIDDEN_IMPORTS:
                    result.is_valid = False
                    result.errors.append(f"금지된 import: {node.module}")
                elif module_name in ALLOWED_BUT_RESTRICTED:
                    # 허용된 모듈이지만 제한된 사용만 허용
                    allowed_attrs = ALLOWED_BUT_RESTRICTED[module_name]
                    imported_names = [alias.name for alias in node.names]
                    # 허용되지 않은 속성 import 검사
                    for name in imported_names:
                        if name not in allowed_attrs and name != "*":
                            result.warnings.append(
                                f"{module_name}에서 {name} import는 권장되지 않습니다 (허용: {allowed_attrs})"
                            )

        # 금지된 함수 호출 검사
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_FUNCTIONS:
                result.is_valid = False
                result.errors.append(f"금지된 함수 호출: {node.func.id}()")
            # open() 함수 호출 검사
            elif isinstance(node.func, ast.Name) and node.func.id == "open":
                result.is_valid = False
                result.errors.append("금지된 함수 호출: open() (파일 접근 금지)")

    return result


def validate_structure(code: str) -> ValidationResult:
    """Level 2: 구조 검증 (Strategy 상속, 필수 메서드).

    Args:
        code: 검증할 코드 문자열

    Returns:
        ValidationResult
    """
    result = ValidationResult(is_valid=True, level="structure")

    try:
        tree = ast.parse(code)
    except SyntaxError:
        result.is_valid = False
        result.errors.append("문법 오류로 인해 구조 검증 불가")
        return result

    # Strategy import 확인
    has_strategy_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "strategy" in node.module and "base" in node.module:
                for alias in node.names:
                    if alias.name == "Strategy":
                        has_strategy_import = True
                        break

    if not has_strategy_import:
        result.is_valid = False
        result.errors.append("Strategy 클래스를 import하지 않았습니다 (from strategy.base import Strategy 필요)")

    # 클래스 정의 찾기
    strategy_classes: list[ast.ClassDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Strategy를 상속하는 클래스 찾기
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id == "Strategy":
                    strategy_classes.append(node)
                    break
                elif isinstance(base, ast.Attribute):
                    # strategy.base.Strategy 같은 경우는 런타임 검증에서 확인
                    pass

    if not strategy_classes:
        result.is_valid = False
        result.errors.append("Strategy를 상속하는 클래스를 찾을 수 없습니다 (클래스 정의에서 Strategy를 상속해야 함)")
        return result

    # 첫 번째 Strategy 클래스 검증
    strategy_class = strategy_classes[0]

    # 클래스 이름이 *Strategy로 끝나는지 확인 (경고)
    if not strategy_class.name.endswith("Strategy"):
        result.warnings.append(f"클래스 이름이 '*Strategy'로 끝나지 않습니다: {strategy_class.name}")

    # 필수 메서드 확인
    method_names = {method.name for method in strategy_class.body if isinstance(method, ast.FunctionDef)}

    if "initialize" not in method_names:
        result.is_valid = False
        result.errors.append("필수 메서드 'initialize'가 없습니다")

    if "on_bar" not in method_names:
        result.is_valid = False
        result.errors.append("필수 메서드 'on_bar'가 없습니다")

    # 메서드 시그니처 확인 (선택적)
    for method in strategy_class.body:
        if isinstance(method, ast.FunctionDef):
            if method.name == "initialize":
                # 최소한 1개의 파라미터 (ctx)가 있어야 함
                if len(method.args.args) < 1:
                    result.warnings.append("initialize() 메서드에 ctx 파라미터가 없을 수 있습니다")
            elif method.name == "on_bar":
                # 최소한 2개의 파라미터 (ctx, bar)가 있어야 함
                if len(method.args.args) < 2:
                    result.warnings.append("on_bar() 메서드에 ctx, bar 파라미터가 없을 수 있습니다")

    return result


def validate_runtime(code: str, sample_data_path: Path | None = None) -> ValidationResult:
    """Level 3: 런타임 스모크 테스트 (실제 실행).

    Args:
        code: 검증할 코드 문자열
        sample_data_path: 샘플 데이터 파일 경로 (기본값: data/sample_btc_1m.csv)

    Returns:
        ValidationResult
    """
    result = ValidationResult(is_valid=True, level="runtime")

    if sample_data_path is None:
        project_root = Path(__file__).parent.parent.parent
        sample_data_path = project_root / "data" / "sample_btc_1m.csv"

    if not sample_data_path.exists():
        result.is_valid = False
        result.errors.append(f"샘플 데이터 파일을 찾을 수 없습니다: {sample_data_path}")
        return result

    # 임시 파일 생성
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_file = Path(f.name)

        # 동적 로드
        spec = importlib.util.spec_from_file_location("temp_strategy", temp_file)
        if not spec or not spec.loader:
            result.is_valid = False
            result.errors.append("전략 파일을 로드할 수 없습니다")
            return result

        module = importlib.util.module_from_spec(spec)
        # 고유한 모듈 이름 사용
        module_name = f"temp_strategy_{id(spec)}"
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            result.is_valid = False
            result.errors.append(f"모듈 로드 실패: {str(e)}")
            result.details["exception_type"] = type(e).__name__
            result.details["exception_message"] = str(e)
            return result

        # 전략 클래스 찾기
        strategy_class = None
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
                # Strategy를 상속하는지 확인
                try:
                    from strategy.base import Strategy

                    if issubclass(obj, Strategy):
                        strategy_class = obj
                        break
                except Exception:
                    # 상속 확인 실패 시 일단 사용
                    strategy_class = obj
                    break

        if not strategy_class:
            result.is_valid = False
            result.errors.append("Strategy를 상속하는 클래스를 찾을 수 없습니다")
            return result

        # 전략 인스턴스화
        try:
            # 기본 파라미터로 인스턴스 생성 시도
            instance = strategy_class()
        except TypeError:
            # 파라미터가 필요한 경우 빈 kwargs로 시도
            try:
                instance = strategy_class(**{})
            except Exception as e:
                result.is_valid = False
                result.errors.append(f"전략 인스턴스화 실패: {str(e)}")
                result.details["exception_type"] = type(e).__name__
                result.details["exception_message"] = str(e)
                return result
        except Exception as e:
            result.is_valid = False
            result.errors.append(f"전략 인스턴스화 실패: {str(e)}")
            result.details["exception_type"] = type(e).__name__
            result.details["exception_message"] = str(e)
            return result

        # Mock Context 생성
        ctx = MockContext()

        # initialize() 호출
        try:
            instance.initialize(ctx)
        except Exception as e:
            result.is_valid = False
            result.errors.append(f"initialize() 호출 실패: {str(e)}")
            result.details["exception_type"] = type(e).__name__
            result.details["exception_message"] = str(e)
            result.details["exception_traceback"] = str(e.__traceback__) if hasattr(e, "__traceback__") else None
            return result

        # 샘플 bar 데이터 생성
        try:
            df = pd.read_csv(sample_data_path, nrows=1)
            if len(df) == 0:
                result.is_valid = False
                result.errors.append("샘플 데이터가 비어있습니다")
                return result

            row = df.iloc[0]
            bar = {
                "timestamp": int(row["timestamp"]),
                "bar_timestamp": int(row["timestamp"]),
                "bar_close": float(row["close"]),
                "price": float(row["close"]),
                "is_new_bar": True,
                "volume": float(row["volume"]),
            }
        except Exception as e:
            result.is_valid = False
            result.errors.append(f"샘플 데이터 로드 실패: {str(e)}")
            return result

        # on_bar() 호출
        try:
            instance.on_bar(ctx, bar)
        except Exception as e:
            result.is_valid = False
            result.errors.append(f"on_bar() 호출 실패: {str(e)}")
            result.details["exception_type"] = type(e).__name__
            result.details["exception_message"] = str(e)
            result.details["exception_traceback"] = str(e.__traceback__) if hasattr(e, "__traceback__") else None
            return result

        # 성공
        result.details["strategy_class"] = strategy_class.__name__
        result.details["instance_created"] = True

    except Exception as e:
        result.is_valid = False
        result.errors.append(f"런타임 검증 중 예상치 못한 오류: {str(e)}")
        result.details["exception_type"] = type(e).__name__
        result.details["exception_message"] = str(e)

    finally:
        # 임시 파일 정리
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass

        # 모듈 캐시 정리
        if module_name in sys.modules:
            del sys.modules[module_name]

    return result


def validate_all(code: str, sample_data_path: Path | None = None) -> ValidationResult:
    """모든 검증 단계를 순차적으로 실행.

    Args:
        code: 검증할 코드 문자열
        sample_data_path: 샘플 데이터 파일 경로

    Returns:
        최종 ValidationResult (모든 레벨의 결과 통합)
    """
    # Level 1: Static 검증
    static_result = validate_static(code)
    if not static_result.is_valid:
        return static_result

    # Level 2: Structure 검증
    structure_result = validate_structure(code)
    if not structure_result.is_valid:
        return structure_result

    # Level 3: Runtime 검증
    runtime_result = validate_runtime(code, sample_data_path)
    if not runtime_result.is_valid:
        return runtime_result

    # 모든 검증 통과
    final_result = ValidationResult(is_valid=True, level="all")
    final_result.warnings = static_result.warnings + structure_result.warnings + runtime_result.warnings
    final_result.details = {
        "static": static_result.details,
        "structure": structure_result.details,
        "runtime": runtime_result.details,
    }

    return final_result
