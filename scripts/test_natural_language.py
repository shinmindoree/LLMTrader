"""자연어 입력 테스트 스크립트.

다양한 자연어 입력에 대한 전략 생성 파이프라인 테스트.
"""

import asyncio
import sys
from pathlib import Path

# 프로젝트 루트 경로 설정
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from llm.intent_parser import IntentType
from llm.pipeline import StrategyGenerationPipeline


# 테스트 케이스 정의
TEST_CASES = [
    {
        "name": "간단한 RSI 전략",
        "input": "RSI가 30에서 롱 진입, 70에서 청산",
        "expected_intent": IntentType.VALID_STRATEGY,
        "should_succeed": True,
    },
    {
        "name": "복합 전략 (RSI + MACD)",
        "input": "RSI가 30 아래이고 MACD가 시그널선을 상향 돌파하면 롱 진입, RSI가 70을 넘으면 청산",
        "expected_intent": IntentType.VALID_STRATEGY,
        "should_succeed": True,
    },
    {
        "name": "볼린저 밴드 전략",
        "input": "볼린저 밴드 하단 터치 시 롱 진입, 상단 터치 시 청산",
        "expected_intent": IntentType.VALID_STRATEGY,
        "should_succeed": True,
    },
    {
        "name": "모호한 입력 (진입/청산 조건 불명확)",
        "input": "RSI 전략",
        "expected_intent": IntentType.INCOMPLETE,
        "should_succeed": False,
    },
    {
        "name": "Off-topic 입력",
        "input": "날씨가 좋으면 매수",
        "expected_intent": IntentType.OFF_TOPIC,
        "should_succeed": False,
    },
    {
        "name": "빈 입력",
        "input": "",
        "expected_intent": IntentType.OFF_TOPIC,
        "should_succeed": False,
    },
    {
        "name": "긴 입력 (1000자 이상)",
        "input": "RSI가 30에서 롱 진입, 70에서 청산" * 50,  # 약 1000자
        "expected_intent": IntentType.VALID_STRATEGY,
        "should_succeed": True,
    },
    {
        "name": "특수문자 포함",
        "input": "RSI가 30에서 롱 진입, 70에서 청산! @#$%",
        "expected_intent": IntentType.VALID_STRATEGY,
        "should_succeed": True,
    },
]


async def test_generation(user_input: str, test_name: str) -> dict:
    """전략 생성 테스트.
    
    Args:
        user_input: 사용자 입력
        test_name: 테스트 이름
        
    Returns:
        테스트 결과 딕셔너리
    """
    result = {
        "test_name": test_name,
        "input": user_input[:100] + "..." if len(user_input) > 100 else user_input,
        "success": False,
        "intent_type": None,
        "has_code": False,
        "validation_passed": False,
        "errors": [],
        "warnings": [],
    }
    
    try:
        # 샘플 데이터 경로 설정
        sample_data_path = project_root / "data" / "sample_btc_1m.csv"
        if not sample_data_path.exists():
            sample_data_path = None
        
        # 파이프라인 생성 및 실행
        pipeline = StrategyGenerationPipeline(sample_data_path=sample_data_path)
        generation_result = await pipeline.generate(user_input)
        
        result["success"] = generation_result.success
        result["has_code"] = generation_result.code is not None
        
        if generation_result.intent_result:
            result["intent_type"] = generation_result.intent_result.intent_type.value
        
        if generation_result.validation_result:
            result["validation_passed"] = generation_result.validation_result.is_valid
        
        result["errors"] = generation_result.errors
        result["warnings"] = generation_result.warnings
        
    except Exception as e:
        result["errors"].append(f"예외 발생: {str(e)}")
        import traceback
        result["traceback"] = traceback.format_exc()
    
    return result


async def main():
    """메인 함수."""
    print("=" * 60)
    print("자연어 입력 테스트: 다양한 입력 시나리오 검증")
    print("=" * 60)
    print()
    
    results = []
    for test_case in TEST_CASES:
        print(f"📋 테스트: {test_case['name']}")
        print(f"   입력: {test_case['input'][:80]}..." if len(test_case['input']) > 80 else f"   입력: {test_case['input']}")
        print("-" * 60)
        
        result = await test_generation(test_case["input"], test_case["name"])
        results.append((test_case, result))
        
        # 결과 출력
        if result["intent_type"]:
            print(f"   의도 타입: {result['intent_type']}")
            expected = test_case["expected_intent"].value
            if result["intent_type"] == expected:
                print(f"   ✅ 의도 타입 일치 (예상: {expected})")
            else:
                print(f"   ⚠️  의도 타입 불일치 (예상: {expected}, 실제: {result['intent_type']})")
        
        if result["has_code"]:
            print("   ✅ 코드 생성됨")
        else:
            print("   ❌ 코드 생성 실패")
        
        if result["validation_passed"]:
            print("   ✅ 검증 통과")
        elif result["has_code"]:
            print("   ⚠️  검증 실패 (코드는 생성됨)")
        
        if result["errors"]:
            print("   ⚠️  오류:")
            for error in result["errors"][:3]:  # 최대 3개만 표시
                print(f"      - {error}")
        
        if result["warnings"]:
            print(f"   ⚠️  경고: {len(result['warnings'])}개")
        
        print()
    
    # 결과 요약
    print("=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for test_case, result in results:
        expected_success = test_case["should_succeed"]
        actual_success = result["success"] and result["has_code"] and result["validation_passed"]
        
        if expected_success == actual_success:
            passed += 1
            status = "✅"
        else:
            failed += 1
            status = "❌"
        
        print(f"{status} {test_case['name']}: {'성공' if actual_success else '실패'} (예상: {'성공' if expected_success else '실패'})")
    
    print()
    print(f"통과: {passed}/{len(TEST_CASES)}")
    print(f"실패: {failed}/{len(TEST_CASES)}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
