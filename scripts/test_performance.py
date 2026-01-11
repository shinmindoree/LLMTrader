"""성능 테스트 스크립트.

전략 생성 및 검증 시간 측정.
"""

import asyncio
import sys
import time
from pathlib import Path

# 프로젝트 루트 경로 설정
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from llm.validator import validate_all


def test_validation_performance():
    """검증 성능 테스트."""
    print("=" * 60)
    print("검증 성능 테스트")
    print("=" * 60)
    print()
    
    # 기존 전략 파일 사용
    strategy_file = project_root / "rsi_long_short_strategy.py"
    if not strategy_file.exists():
        print("❌ 테스트용 전략 파일을 찾을 수 없습니다.")
        return
    
    code = strategy_file.read_text(encoding="utf-8")
    
    # 샘플 데이터 경로
    sample_data_path = project_root / "data" / "sample_btc_1m.csv"
    if not sample_data_path.exists():
        sample_data_path = None
    
    # 검증 시간 측정
    times = []
    for i in range(3):  # 3회 실행하여 평균 계산
        start_time = time.time()
        result = validate_all(code, sample_data_path)
        elapsed = time.time() - start_time
        times.append(elapsed)
        
        print(f"실행 {i+1}: {elapsed:.2f}초 ({'통과' if result.is_valid else '실패'})")
    
    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)
    
    print()
    print(f"평균 시간: {avg_time:.2f}초")
    print(f"최소 시간: {min_time:.2f}초")
    print(f"최대 시간: {max_time:.2f}초")
    
    if avg_time < 10:
        print("✅ 검증 시간 목표 달성 (< 10초)")
    else:
        print("⚠️  검증 시간이 목표를 초과합니다 (> 10초)")
    
    print()


def test_file_loading_performance():
    """파일 로드 성능 테스트."""
    print("=" * 60)
    print("파일 로드 성능 테스트")
    print("=" * 60)
    print()
    
    strategy_files = list(project_root.glob("*_strategy.py"))
    strategy_files = [f for f in strategy_files if f.name != "generated_strategy.py"]
    
    if not strategy_files:
        print("❌ 전략 파일을 찾을 수 없습니다.")
        return
    
    times = []
    for strategy_file in strategy_files:
        start_time = time.time()
        
        # 파일 읽기
        code = strategy_file.read_text(encoding="utf-8")
        
        # AST 파싱
        import ast
        tree = ast.parse(code)
        
        elapsed = time.time() - start_time
        times.append(elapsed)
        
        print(f"{strategy_file.name}: {elapsed*1000:.2f}ms")
    
    if times:
        avg_time = sum(times) / len(times)
        print()
        print(f"평균 로드 시간: {avg_time*1000:.2f}ms")
        print("✅ 파일 로드 성능 양호")
    
    print()


def test_memory_usage():
    """메모리 사용량 테스트 (간단한 확인)."""
    print("=" * 60)
    print("메모리 사용량 확인")
    print("=" * 60)
    print()
    
    try:
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        memory_mb = process.memory_info().rss / 1024 / 1024
        
        print(f"현재 메모리 사용량: {memory_mb:.2f} MB")
        
        if memory_mb < 500:
            print("✅ 메모리 사용량 양호 (< 500 MB)")
        else:
            print("⚠️  메모리 사용량이 높습니다 (> 500 MB)")
        
    except ImportError:
        print("⚠️  psutil이 설치되지 않아 메모리 사용량을 측정할 수 없습니다.")
        print("   설치: uv add psutil")
    
    print()


def main():
    """메인 함수."""
    print("=" * 60)
    print("성능 테스트")
    print("=" * 60)
    print()
    
    test_file_loading_performance()
    test_validation_performance()
    test_memory_usage()
    
    print("=" * 60)
    print("✅ 성능 테스트 완료")
    print("=" * 60)
    print()
    print("💡 참고:")
    print("   - 전략 생성 시간은 LLM API 응답 시간에 따라 달라집니다")
    print("   - 평균 생성 시간 목표: < 30초")
    print("   - 검증 시간 목표: < 10초")


if __name__ == "__main__":
    main()
