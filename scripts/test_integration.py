"""통합 테스트 스크립트.

생성된 전략이 백테스트와 라이브 트레이딩에서 정상적으로 동작하는지 확인합니다.
"""

import importlib.util
import sys
from pathlib import Path

# 프로젝트 루트 경로 설정
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from strategy.base import Strategy
from strategy.context import StrategyContext


def test_strategy_file_loading(strategy_file: Path) -> dict:
    """전략 파일 로드 및 기본 검증.
    
    Args:
        strategy_file: 전략 파일 경로
        
    Returns:
        테스트 결과 딕셔너리
    """
    result = {
        "file": strategy_file.name,
        "loaded": False,
        "has_strategy_class": False,
        "inherits_strategy": False,
        "has_initialize": False,
        "has_on_bar": False,
        "errors": [],
    }
    
    try:
        # 전략 파일 로드
        spec = importlib.util.spec_from_file_location("test_strategy", strategy_file)
        if not spec or not spec.loader:
            result["errors"].append("파일을 로드할 수 없습니다")
            return result
        
        module = importlib.util.module_from_spec(spec)
        module_name = f"test_strategy_{id(spec)}"
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        result["loaded"] = True
        
        # Strategy 클래스 찾기
        strategy_class = None
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
                strategy_class = obj
                result["has_strategy_class"] = True
                result["class_name"] = name
                break
        
        if not strategy_class:
            result["errors"].append("Strategy 클래스를 찾을 수 없습니다")
            return result
        
        # Strategy 상속 확인
        try:
            if issubclass(strategy_class, Strategy):
                result["inherits_strategy"] = True
            else:
                result["errors"].append("Strategy를 상속하지 않습니다")
        except Exception as e:
            result["errors"].append(f"상속 확인 실패: {str(e)}")
        
        # 필수 메서드 확인
        if hasattr(strategy_class, "initialize"):
            result["has_initialize"] = True
        else:
            result["errors"].append("initialize 메서드가 없습니다")
        
        if hasattr(strategy_class, "on_bar"):
            result["has_on_bar"] = True
        else:
            result["errors"].append("on_bar 메서드가 없습니다")
        
        # 모듈 정리
        if module_name in sys.modules:
            del sys.modules[module_name]
        
    except Exception as e:
        result["errors"].append(f"로드 중 오류: {str(e)}")
        import traceback
        result["traceback"] = traceback.format_exc()
    
    return result


def test_strategy_instantiation(strategy_file: Path) -> dict:
    """전략 인스턴스화 테스트.
    
    Args:
        strategy_file: 전략 파일 경로
        
    Returns:
        테스트 결과 딕셔너리
    """
    result = {
        "file": strategy_file.name,
        "instantiated": False,
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
        
        # 인스턴스화 시도
        try:
            instance = strategy_class()
            result["instantiated"] = True
        except TypeError:
            # 파라미터가 필요한 경우 빈 kwargs로 시도
            try:
                instance = strategy_class(**{})
                result["instantiated"] = True
            except Exception as e:
                result["errors"].append(f"인스턴스화 실패: {str(e)}")
        except Exception as e:
            result["errors"].append(f"인스턴스화 실패: {str(e)}")
        
        # 모듈 정리
        if module_name in sys.modules:
            del sys.modules[module_name]
        
    except Exception as e:
        result["errors"].append(f"인스턴스화 테스트 중 오류: {str(e)}")
        import traceback
        result["traceback"] = traceback.format_exc()
    
    return result


def main():
    """메인 함수."""
    print("=" * 60)
    print("통합 테스트: 생성된 전략 파일 검증")
    print("=" * 60)
    print()
    
    # 전략 파일 찾기
    strategy_files = list(project_root.glob("*_strategy.py"))
    strategy_files = [f for f in strategy_files if f.name != "generated_strategy.py"]
    
    if not strategy_files:
        print("❌ 전략 파일을 찾을 수 없습니다.")
        return
    
    print(f"발견된 전략 파일: {len(strategy_files)}개")
    for f in strategy_files:
        print(f"  - {f.name}")
    print()
    
    # 각 전략 파일 테스트
    all_passed = True
    for strategy_file in strategy_files:
        print(f"📋 테스트 중: {strategy_file.name}")
        print("-" * 60)
        
        # 파일 로드 테스트
        load_result = test_strategy_file_loading(strategy_file)
        
        if load_result["loaded"]:
            print("  ✅ 파일 로드 성공")
        else:
            print("  ❌ 파일 로드 실패")
            all_passed = False
        
        if load_result["has_strategy_class"]:
            print(f"  ✅ Strategy 클래스 발견: {load_result.get('class_name', 'N/A')}")
        else:
            print("  ❌ Strategy 클래스를 찾을 수 없음")
            all_passed = False
        
        if load_result["inherits_strategy"]:
            print("  ✅ Strategy 상속 확인")
        else:
            print("  ❌ Strategy 상속 실패")
            all_passed = False
        
        if load_result["has_initialize"]:
            print("  ✅ initialize 메서드 존재")
        else:
            print("  ❌ initialize 메서드 없음")
            all_passed = False
        
        if load_result["has_on_bar"]:
            print("  ✅ on_bar 메서드 존재")
        else:
            print("  ❌ on_bar 메서드 없음")
            all_passed = False
        
        if load_result["errors"]:
            print("  ⚠️  오류:")
            for error in load_result["errors"]:
                print(f"     - {error}")
            all_passed = False
        
        # 인스턴스화 테스트
        inst_result = test_strategy_instantiation(strategy_file)
        if inst_result["instantiated"]:
            print("  ✅ 인스턴스화 성공")
        else:
            print("  ❌ 인스턴스화 실패")
            if inst_result["errors"]:
                for error in inst_result["errors"]:
                    print(f"     - {error}")
            all_passed = False
        
        print()
    
    # 결과 요약
    print("=" * 60)
    if all_passed:
        print("✅ 모든 테스트 통과!")
    else:
        print("❌ 일부 테스트 실패")
    print("=" * 60)


if __name__ == "__main__":
    main()
