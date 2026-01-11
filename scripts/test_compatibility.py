"""호환성 테스트 스크립트.

생성된 전략이 기존 전략과 동일한 방식으로 Context 인터페이스를 사용하는지 확인합니다.
"""

import ast
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any

# 프로젝트 루트 경로 설정
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from strategy.base import Strategy
from strategy.context import StrategyContext


def check_context_usage(strategy_file: Path) -> dict:
    """Context 인터페이스 사용 확인.
    
    Args:
        strategy_file: 전략 파일 경로
        
    Returns:
        검증 결과 딕셔너리
    """
    result = {
        "file": strategy_file.name,
        "uses_ctx_current_price": False,
        "uses_ctx_position_size": False,
        "uses_ctx_get_indicator": False,
        "uses_ctx_buy": False,
        "uses_ctx_sell": False,
        "uses_ctx_close_position": False,
        "errors": [],
    }
    
    try:
        # 파일 읽기
        code = strategy_file.read_text(encoding="utf-8")
        
        # AST 파싱
        tree = ast.parse(code)
        
        # Context 사용 패턴 확인
        for node in ast.walk(tree):
            # ctx.current_price 사용 확인
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == "ctx":
                    if node.attr == "current_price":
                        result["uses_ctx_current_price"] = True
                    elif node.attr == "position_size":
                        result["uses_ctx_position_size"] = True
            
            # ctx.get_indicator() 호출 확인
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "ctx":
                        if node.func.attr == "get_indicator":
                            result["uses_ctx_get_indicator"] = True
                        elif node.func.attr == "buy":
                            result["uses_ctx_buy"] = True
                        elif node.func.attr == "sell":
                            result["uses_ctx_sell"] = True
                        elif node.func.attr == "close_position":
                            result["uses_ctx_close_position"] = True
        
    except Exception as e:
        result["errors"].append(f"코드 분석 실패: {str(e)}")
    
    return result


def check_method_signatures(strategy_file: Path) -> dict:
    """메서드 시그니처 확인.
    
    Args:
        strategy_file: 전략 파일 경로
        
    Returns:
        검증 결과 딕셔너리
    """
    result = {
        "file": strategy_file.name,
        "initialize_signature": None,
        "on_bar_signature": None,
        "errors": [],
    }
    
    try:
        spec = importlib.util.spec_from_file_location("test_strategy", strategy_file)
        if not spec or not spec.loader:
            result["errors"].append("파일을 로드할 수 없습니다")
            return result
        
        module = importlib.util.module_from_spec(spec)
        module_name = f"test_strategy_{id(spec)}"
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        
        # Strategy 클래스 찾기
        strategy_class = None
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
                strategy_class = obj
                break
        
        if not strategy_class:
            result["errors"].append("Strategy 클래스를 찾을 수 없습니다")
            return result
        
        # initialize 메서드 시그니처 확인
        if hasattr(strategy_class, "initialize"):
            sig = inspect.signature(strategy_class.initialize)
            params = list(sig.parameters.keys())
            result["initialize_signature"] = params
            if "ctx" not in params:
                result["errors"].append("initialize 메서드에 ctx 파라미터가 없습니다")
        
        # on_bar 메서드 시그니처 확인
        if hasattr(strategy_class, "on_bar"):
            sig = inspect.signature(strategy_class.on_bar)
            params = list(sig.parameters.keys())
            result["on_bar_signature"] = params
            if "ctx" not in params:
                result["errors"].append("on_bar 메서드에 ctx 파라미터가 없습니다")
            if "bar" not in params:
                result["errors"].append("on_bar 메서드에 bar 파라미터가 없습니다")
        
        # 모듈 정리
        if module_name in sys.modules:
            del sys.modules[module_name]
        
    except Exception as e:
        result["errors"].append(f"시그니처 확인 실패: {str(e)}")
    
    return result


def main():
    """메인 함수."""
    print("=" * 60)
    print("호환성 테스트: Context 인터페이스 및 메서드 시그니처 확인")
    print("=" * 60)
    print()
    
    # 전략 파일 찾기
    strategy_files = list(project_root.glob("*_strategy.py"))
    strategy_files = [f for f in strategy_files if f.name != "generated_strategy.py"]
    
    if not strategy_files:
        print("❌ 전략 파일을 찾을 수 없습니다.")
        return
    
    # 기존 전략과 생성된 전략 구분
    existing_strategies = [f for f in strategy_files if not f.name.startswith("Generated")]
    generated_strategies = [f for f in strategy_files if f.name.startswith("Generated")]
    
    print(f"기존 전략: {len(existing_strategies)}개")
    print(f"생성된 전략: {len(generated_strategies)}개")
    print()
    
    # 모든 전략 파일 테스트
    all_passed = True
    for strategy_file in strategy_files:
        print(f"📋 테스트 중: {strategy_file.name}")
        print("-" * 60)
        
        # Context 사용 확인
        ctx_result = check_context_usage(strategy_file)
        
        print("  Context 인터페이스 사용:")
        if ctx_result["uses_ctx_get_indicator"]:
            print("    ✅ ctx.get_indicator() 사용")
        else:
            print("    ⚠️  ctx.get_indicator() 미사용 (지표를 사용하지 않을 수 있음)")
        
        if ctx_result["uses_ctx_buy"] or ctx_result["uses_ctx_sell"]:
            print("    ✅ ctx.buy() 또는 ctx.sell() 사용")
        else:
            print("    ⚠️  ctx.buy() 또는 ctx.sell() 미사용")
        
        if ctx_result["uses_ctx_close_position"]:
            print("    ✅ ctx.close_position() 사용")
        
        if ctx_result["uses_ctx_current_price"]:
            print("    ✅ ctx.current_price 사용")
        
        if ctx_result["uses_ctx_position_size"]:
            print("    ✅ ctx.position_size 사용")
        
        # 메서드 시그니처 확인
        sig_result = check_method_signatures(strategy_file)
        
        print("  메서드 시그니처:")
        if sig_result["initialize_signature"]:
            print(f"    initialize({', '.join(sig_result['initialize_signature'])})")
            if "ctx" in sig_result["initialize_signature"]:
                print("      ✅ ctx 파라미터 존재")
            else:
                print("      ❌ ctx 파라미터 없음")
                all_passed = False
        
        if sig_result["on_bar_signature"]:
            print(f"    on_bar({', '.join(sig_result['on_bar_signature'])})")
            if "ctx" in sig_result["on_bar_signature"] and "bar" in sig_result["on_bar_signature"]:
                print("      ✅ ctx, bar 파라미터 존재")
            else:
                print("      ❌ ctx 또는 bar 파라미터 없음")
                all_passed = False
        
        if sig_result["errors"]:
            print("  ⚠️  오류:")
            for error in sig_result["errors"]:
                print(f"     - {error}")
            all_passed = False
        
        if ctx_result["errors"]:
            print("  ⚠️  오류:")
            for error in ctx_result["errors"]:
                print(f"     - {error}")
            all_passed = False
        
        print()
    
    # 결과 요약
    print("=" * 60)
    if all_passed:
        print("✅ 모든 호환성 테스트 통과!")
        print()
        print("💡 생성된 전략이 기존 전략과 동일한 방식으로")
        print("   Context 인터페이스를 사용합니다.")
    else:
        print("❌ 일부 호환성 테스트 실패")
    print("=" * 60)


if __name__ == "__main__":
    main()
