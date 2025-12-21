#!/usr/bin/env python3
"""Azure Application Insights 로깅 테스트 스크립트.

사용법:
    # 1. .env에 APPLICATIONINSIGHTS_CONNECTION_STRING 설정 후
    uv run python scripts/test_azure_logging.py

    # 2. Azure Portal에서 확인:
    #    Application Insights → Logs → 쿼리:
    #    traces | where message contains "TEST" | order by timestamp desc
"""

import time

from llmtrader.logging import get_logger
from llmtrader.settings import get_settings


def main() -> None:
    settings = get_settings()

    print("=" * 60)
    print("Azure Application Insights 로깅 테스트")
    print("=" * 60)

    # 연결 문자열 확인
    conn_str = settings.azure.connection_string
    if conn_str:
        # 키 일부만 표시 (보안)
        masked = conn_str[:50] + "..." if len(conn_str) > 50 else conn_str
        print(f"✅ Connection String: {masked}")
    else:
        print("❌ APPLICATIONINSIGHTS_CONNECTION_STRING not set in .env")
        print("\n다음 단계:")
        print("1. Azure Portal → Application Insights 생성")
        print("2. 연결 문자열 복사")
        print("3. .env 파일에 추가:")
        print("   APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=xxx;...")
        return

    # 로거 초기화
    logger = get_logger("llmtrader.test")

    print(f"\n📊 Azure 활성화: {logger.is_azure_enabled}")
    print("\n테스트 로그 전송 중...")

    # 테스트 로그 전송
    logger.info("TEST_INFO: 정상 동작 테스트", test_type="info")
    print("  ✓ INFO 로그 전송")

    logger.warning("TEST_WARNING: 경고 테스트", test_type="warning")
    print("  ✓ WARNING 로그 전송")

    logger.log_tick(
        symbol="BTCUSDT",
        bar_time="2024-12-21T10:30",
        price=98000.0,
        rsi=45.2,
        rsi_rt=44.8,
        position=0.01,
        balance=5000.0,
        pnl=50.0,
    )
    print("  ✓ TICK 로그 전송")

    logger.log_order(
        event="TEST_ENTRY",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.01,
        price=98000.0,
        order_id="test-12345",
        rsi=30.5,
    )
    print("  ✓ ORDER 로그 전송")

    logger.log_error(
        error_type="TEST_ERROR",
        message="이것은 테스트 에러입니다 (정상 동작)",
        symbol="BTCUSDT",
    )
    print("  ✓ ERROR 로그 전송 (Alert 트리거 테스트)")

    logger.log_session_start(
        symbol="BTCUSDT",
        strategy="TestStrategy",
        leverage=5,
        max_position=1.0,
    )
    print("  ✓ SESSION_START 로그 전송")

    logger.log_session_end(
        symbol="BTCUSDT",
        total_trades=10,
        total_pnl=150.0,
        win_rate=0.6,
        duration_minutes=30.5,
    )
    print("  ✓ SESSION_END 로그 전송")

    print("\n" + "=" * 60)
    print("✅ 테스트 로그 전송 완료!")
    print("=" * 60)

    if logger.is_azure_enabled:
        print("\n⏳ Azure로 로그 전송 중... (최대 2분 소요)")
        print("\n📌 확인 방법:")
        print("1. Azure Portal → Application Insights 열기")
        print("2. 왼쪽 메뉴: Logs (로그)")
        print("3. 쿼리 실행:")
        print()
        print("   traces")
        print('   | where message contains "TEST"')
        print("   | order by timestamp desc")
        print("   | take 20")
        print()
        print("4. 에러 확인 (Alert 대상):")
        print()
        print("   traces")
        print("   | where severityLevel >= 3")
        print("   | order by timestamp desc")
        print()

        # Azure로 전송 대기 (버퍼 플러시)
        print("로그 버퍼 플러시 대기 (5초)...")
        time.sleep(5)
        print("완료!")
    else:
        print("\n⚠️  Azure SDK가 설치되지 않았거나 연결 문자열이 잘못되었습니다.")
        print("콘솔 로그만 출력되었습니다.")


if __name__ == "__main__":
    main()

