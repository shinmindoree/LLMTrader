"""라이브 트레이딩 통합 테스트 스크립트.

생성된 전략이 라이브 트레이딩 스크립트에서 정상적으로 로드되는지 확인합니다.
"""

import importlib.util
import sys
from pathlib import Path

# 프로젝트 루트 경로 설정
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


def load_strategy_class(strategy_file: Path):
    """전략 클래스 로드 (run_live_trading.py와 동일한 로직).
    
    Args:
        strategy_file: 전략 파일 경로
        
    Returns:
        Strategy 클래스
    """
    spec = importlib.util.spec_from_file_location("custom_strategy", strategy_file)
    if not spec or not spec.loader:
        raise ValueError(f"전략 파일을 로드할 수 없습니다: {strategy_file}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_strategy"] = module
    spec.loader.exec_module(module)

    # Strategy 클래스 찾기
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
            print(f"🧩 전략 클래스 로드됨: {name} (파일: {strategy_file.name})")
            return obj

    raise ValueError(f"전략 클래스를 찾을 수 없습니다: {strategy_file}")


def test_strategy_loading(strategy_file: Path) -> dict:
    """전략 로드 테스트.
    
    Args:
        strategy_file: 전략 파일 경로
        
    Returns:
        테스트 결과 딕셔너리
    """
    result = {
        "file": strategy_file.name,
        "loaded": False,
        "class_name": None,
        "instantiated": False,
        "errors": [],
    }
    
    try:
        strategy_class = load_strategy_class(strategy_file)
        result["loaded"] = True
        result["class_name"] = strategy_class.__name__
        
        # 인스턴스화 테스트
        try:
            instance = strategy_class()
            result["instantiated"] = True
        except TypeError:
            try:
                instance = strategy_class(**{})
                result["instantiated"] = True
            except Exception as e:
                result["errors"].append(f"인스턴스화 실패: {str(e)}")
        except Exception as e:
            result["errors"].append(f"인스턴스화 실패: {str(e)}")
        
    except Exception as e:
        result["errors"].append(f"로드 실패: {str(e)}")
        import traceback
        result["traceback"] = traceback.format_exc()
    
    return result


def main():
    """메인 함수."""
    print("=" * 60)
    print("라이브 트레이딩 통합 테스트: 전략 파일 로드 확인")
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
        
        result = test_strategy_loading(strategy_file)
        
        if result["loaded"]:
            print(f"  ✅ 전략 클래스 로드 성공: {result['class_name']}")
        else:
            print("  ❌ 전략 클래스 로드 실패")
            all_passed = False
        
        if result["instantiated"]:
            print("  ✅ 인스턴스화 성공")
        else:
            print("  ❌ 인스턴스화 실패")
            all_passed = False
        
        if result["errors"]:
            print("  ⚠️  오류:")
            for error in result["errors"]:
                print(f"     - {error}")
            all_passed = False
        
        print()
    
    # 결과 요약
    print("=" * 60)
    if all_passed:
        print("✅ 모든 테스트 통과!")
        print()
        print("💡 다음 단계:")
        print("   라이브 트레이딩 페이지에서 전략을 선택하고")
        print("   생성된 명령어로 테스트넷에서 실행할 수 있습니다.")
    else:
        print("❌ 일부 테스트 실패")
    print("=" * 60)


if __name__ == "__main__":
    main()
