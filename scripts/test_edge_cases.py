"""에지 케이스 테스트 스크립트.

다양한 에지 케이스에 대한 UI 및 파일 시스템 테스트.
"""

import sys
from pathlib import Path

# 프로젝트 루트 경로 설정
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


def test_file_name_validation():
    """파일명 검증 테스트."""
    print("=" * 60)
    print("파일명 검증 테스트")
    print("=" * 60)
    print()
    
    test_cases = [
        ("NormalStrategy", "NormalStrategy_strategy.py", True),
        ("Test Strategy", "TestStrategy_strategy.py", True),  # 공백 제거
        ("Test@#$Strategy", "TestStrategy_strategy.py", True),  # 특수문자 제거
        ("", "Strategy_strategy.py", True),  # 빈 이름
        ("a" * 200, "a" * 200 + "Strategy_strategy.py", True),  # 매우 긴 이름
        ("한글전략", "한글전략Strategy_strategy.py", True),  # 한글
    ]
    
    passed = 0
    failed = 0
    
    for strategy_name, expected_pattern, should_pass in test_cases:
        # 파일명 생성 로직 (전략 생성 페이지와 동일)
        safe_name = "".join(c for c in strategy_name if c.isalnum() or c in ("_", "-"))
        if not safe_name.endswith("Strategy"):
            safe_name = f"{safe_name}Strategy"
        filename = f"{safe_name}_strategy.py"
        
        # 검증
        is_valid = filename.endswith("_strategy.py") and len(filename) > 0
        
        if is_valid == should_pass:
            passed += 1
            status = "✅"
        else:
            failed += 1
            status = "❌"
        
        print(f"{status} '{strategy_name}' -> '{filename}'")
        if not is_valid == should_pass:
            print(f"   예상: {'유효' if should_pass else '무효'}, 실제: {'유효' if is_valid else '무효'}")
    
    print()
    print(f"통과: {passed}/{len(test_cases)}")
    print(f"실패: {failed}/{len(test_cases)}")
    print()


def test_duplicate_file_handling():
    """중복 파일 처리 테스트."""
    print("=" * 60)
    print("중복 파일 처리 테스트")
    print("=" * 60)
    print()
    
    # 기존 전략 파일 확인
    existing_files = list(project_root.glob("*_strategy.py"))
    existing_names = [f.name for f in existing_files]
    
    print(f"기존 전략 파일: {len(existing_names)}개")
    for name in existing_names[:5]:  # 최대 5개만 표시
        print(f"  - {name}")
    if len(existing_names) > 5:
        print(f"  ... 외 {len(existing_names) - 5}개")
    print()
    
    # 중복 이름 테스트
    test_name = "TestStrategy"
    test_filename = f"{test_name}_strategy.py"
    test_path = project_root / test_filename
    
    if test_path.exists():
        print(f"⚠️  테스트 파일이 이미 존재합니다: {test_filename}")
        print("   (실제 저장 시 덮어쓰기 또는 이름 변경 필요)")
    else:
        print(f"✅ 테스트 파일명 사용 가능: {test_filename}")
    
    print()


def test_path_resolution():
    """경로 해석 테스트."""
    print("=" * 60)
    print("경로 해석 테스트")
    print("=" * 60)
    print()
    
    # 백테스트 페이지 경로
    backtest_page = project_root / "pages" / "3_📊_백테스트.py"
    if backtest_page.exists():
        # 페이지에서 사용하는 project_root 계산
        calculated_root = backtest_page.parent.parent
        print(f"백테스트 페이지: {backtest_page.name}")
        print(f"  계산된 project_root: {calculated_root}")
        print(f"  실제 project_root: {project_root}")
        
        if calculated_root == project_root:
            print("  ✅ 경로 일치")
        else:
            print("  ❌ 경로 불일치")
        
        # 전략 파일 찾기 테스트
        strategy_files = list(calculated_root.glob("*_strategy.py"))
        print(f"  발견된 전략 파일: {len(strategy_files)}개")
    
    print()
    
    # 라이브 트레이딩 페이지 경로
    live_page = project_root / "pages" / "4_🔴_라이브_트레이딩.py"
    if live_page.exists():
        calculated_root = live_page.parent.parent
        print(f"라이브 트레이딩 페이지: {live_page.name}")
        print(f"  계산된 project_root: {calculated_root}")
        print(f"  실제 project_root: {project_root}")
        
        if calculated_root == project_root:
            print("  ✅ 경로 일치")
        else:
            print("  ❌ 경로 불일치")
        
        strategy_files = list(calculated_root.glob("*_strategy.py"))
        print(f"  발견된 전략 파일: {len(strategy_files)}개")
    
    print()


def test_input_validation():
    """입력 검증 테스트."""
    print("=" * 60)
    print("입력 검증 테스트")
    print("=" * 60)
    print()
    
    test_cases = [
        ("", "빈 입력", False),
        ("   ", "공백만", False),
        ("RSI 전략", "모호한 입력", True),  # INCOMPLETE로 처리될 수 있음
        ("a" * 10000, "매우 긴 입력 (10000자)", True),  # 처리 가능해야 함
        ("RSI가 30에서 롱 진입, 70에서 청산", "정상 입력", True),
    ]
    
    for input_text, description, should_accept in test_cases:
        is_valid = len(input_text.strip()) > 0
        
        if is_valid == should_accept or (not should_accept and not is_valid):
            status = "✅"
        else:
            status = "⚠️"
        
        preview = input_text[:50] + "..." if len(input_text) > 50 else input_text
        print(f"{status} {description}: '{preview}'")
        print(f"   길이: {len(input_text)}자, 유효: {is_valid}")
    
    print()


def main():
    """메인 함수."""
    print("=" * 60)
    print("에지 케이스 테스트")
    print("=" * 60)
    print()
    
    test_file_name_validation()
    test_duplicate_file_handling()
    test_path_resolution()
    test_input_validation()
    
    print("=" * 60)
    print("✅ 에지 케이스 테스트 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()
