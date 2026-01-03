"""라이브 트레이딩 실행 스크립트."""

import argparse
import asyncio
import importlib.util
import json
import os
import signal
import sys
from pathlib import Path

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.live.context import LiveContext
from llmtrader.live.engine import LiveTradingEngine
from llmtrader.live.risk import RiskConfig, RiskManager
from llmtrader.notifications.slack import SlackNotifier
from llmtrader.live.price_feed import PriceFeed
from llmtrader.settings import get_settings


def parse_args() -> argparse.Namespace:
    """명령줄 인자 파싱."""
    parser = argparse.ArgumentParser(description="라이브 트레이딩 실행")
    parser.add_argument("strategy_file", type=Path, help="전략 파일 경로")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="거래 심볼")
    parser.add_argument(
        "--leverage",
        type=int,
        default=int(os.getenv("LEVERAGE", "1")),
        help="레버리지 (기본: 1). 환경 변수 LEVERAGE로도 설정 가능",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="가격 피드 간격 (초)")
    parser.add_argument(
        "--candle-interval",
        type=str,
        default=os.getenv("CANDLE_INTERVAL", "1m"),
        help="캔들 봉 간격 (예: 1m, 5m, 15m). 환경 변수 CANDLE_INTERVAL로도 설정 가능",
    )
    parser.add_argument(
        "--max-position",
        type=float,
        default=float(os.getenv("MAX_POSITION", "0.5")),
        help="최대 포지션 크기 (자산 대비, 기본: 0.5). 환경 변수 MAX_POSITION로도 설정 가능",
    )
    parser.add_argument("--daily-loss-limit", type=float, default=500.0, help="일일 손실 한도 (USDT)")
    parser.add_argument(
        "--max-consecutive-losses",
        type=int,
        default=0,
        help="최대 연속 손실 횟수 (0이면 비활성화)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="대화형 확인 프롬프트를 건너뛰고 즉시 실행합니다(컨테이너/서버 환경 필수).",
    )
    return parser.parse_args()


def load_strategy_class(strategy_file: Path):
    """전략 클래스 로드."""
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
            # [✅ 추가] 어떤 클래스가 로드되었는지 로그로 출력
            print(f"🧩 전략 클래스 로드됨: {name} (파일: {strategy_file})") 
            return obj

    raise ValueError(f"전략 클래스를 찾을 수 없습니다: {strategy_file}")


async def main():
    """메인 함수."""
    args = parse_args()

    print("=" * 80)
    print("🚀 라이브 트레이딩 시작")
    print("=" * 80)
    print(f"전략 파일: {args.strategy_file}")
    print(f"심볼: {args.symbol}")
    print(f"레버리지: {args.leverage}x")
    print(f"최대 포지션: {args.max_position * 100}% (자산 대비)")
    print(f"캔들 봉 간격: {args.candle_interval}")
    print(f"일일 손실 한도: ${args.daily_loss_limit}")
    if args.max_consecutive_losses > 0:
        print(f"최대 연속 손실: {args.max_consecutive_losses}회")
    else:
        print("최대 연속 손실: 비활성화")
    print("=" * 80)
    print()

    # 경고 메시지
    print("⚠️  경고: 실제 계좌에 주문이 실행됩니다!")
    print("⚠️  테스트넷 API를 사용 중인지 확인하세요.")
    print()

    if not args.yes:
        try:
            response = input("계속하시겠습니까? (yes/no): ")
        except EOFError:
            print("❌ 대화형 입력(stdin)을 사용할 수 없습니다. --yes 옵션을 추가해서 실행하세요.")
            return
        if response.lower() != "yes":
            print("취소되었습니다.")
            return

    # 설정 로드
    settings = get_settings()

    # 바이낸스 클라이언트 생성
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )

    # 리스크 관리자 생성
    risk_config = RiskConfig(
        max_leverage=float(args.leverage),
        max_position_size=args.max_position,
        # 단일 주문 한도는 기본적으로 "최대 포지션 한도"와 동일하게 둔다.
        # 사용자가 --max-position 1.0 으로 설정해 "최대한 진입"을 원할 때,
        # 기본 max_order_size=0.5 때문에 주문이 거절되는 혼란을 방지한다.
        max_order_size=args.max_position,
        daily_loss_limit=args.daily_loss_limit,
        max_consecutive_losses=args.max_consecutive_losses,
    )
    risk_manager = RiskManager(risk_config)

    notifier = SlackNotifier(settings.slack.webhook_url) if settings.slack.webhook_url else None

    # 컨텍스트 생성
    ctx = LiveContext(
        client=client,
        risk_manager=risk_manager,
        symbol=args.symbol,
        leverage=args.leverage,
        env=settings.env,
        notifier=notifier,
    )

    # 전략 로드
    strategy_class = load_strategy_class(args.strategy_file)
    
    # 환경 변수에서 rsi_period 읽기 (기본값: 전략 클래스의 기본값 사용)
    rsi_period = os.getenv("RSI_PERIOD")
    if rsi_period:
        try:
            rsi_period_int = int(rsi_period)
            # rsi_period 파라미터를 지원하는 전략의 경우 전달
            try:
                strategy = strategy_class(rsi_period=rsi_period_int)
            except TypeError:
                # rsi_period 파라미터를 지원하지 않는 전략의 경우 기본값 사용
                strategy = strategy_class()
        except ValueError:
            print(f"⚠️  RSI_PERIOD 환경 변수 값 '{rsi_period}'이 유효하지 않습니다. 기본값 사용.")
            strategy = strategy_class()
    else:
        # 환경 변수가 없으면 전략의 기본값 사용
        strategy = strategy_class()

    # 가격 피드 생성
    price_feed = PriceFeed(client, args.symbol, args.interval, candle_interval=args.candle_interval)

    # 엔진 생성
    engine = LiveTradingEngine(strategy, ctx, price_feed)

    # 시그널 핸들러 설정
    def signal_handler(sig, frame):
        print("\n\n정지 신호를 받았습니다. 종료 중...")
        engine.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 트레이딩 시작
    try:
        await engine.start()
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 종료 시 요약 출력
        print("\n" + "=" * 80)
        print("📊 라이브 트레이딩 요약")
        print("=" * 80)
        summary = engine.get_summary()
        print(json.dumps(summary, indent=2))

        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())

