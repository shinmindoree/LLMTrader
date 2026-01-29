"""백테스트 실행 스크립트."""

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

# src 디렉토리를 Python 경로에 추가
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from backtest.context import BacktestContext
from backtest.data_fetcher import fetch_all_klines
from backtest.engine import BacktestEngine
from backtest.risk import BacktestRiskManager
from binance.client import BinanceHTTPClient
from common.risk import RiskConfig
from settings import get_settings
from strategy.base import Strategy


def parse_args() -> argparse.Namespace:
    """명령줄 인자 파싱."""
    parser = argparse.ArgumentParser(description="백테스트 실행")
    parser.add_argument("strategy_file", type=Path, help="전략 파일 경로")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="거래 심볼")
    parser.add_argument("--leverage", type=int, default=1, help="레버리지")
    parser.add_argument("--candle-interval", type=str, default="1h", help="캔들 간격 (예: 1m, 5m, 15m, 1h, 4h, 1d)")
    parser.add_argument("--max-position", type=float, default=0.5, help="최대 포지션 크기 (자산 대비, 기본: 0.5)")
    parser.add_argument("--initial-balance", type=float, default=1000.0, help="초기 자산 (USDT, 기본: 1000)")
    parser.add_argument("--start-date", type=str, required=True, help="시작 날짜 (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="종료 날짜 (YYYY-MM-DD)")
    parser.add_argument("--commission", type=float, default=0.0004, help="수수료율 (기본 0.0004 = 0.04%%)")
    parser.add_argument(
        "--stop-loss-pct",
        type=float,
        default=0.05,
        help="StopLoss 비율 (0.0~1.0, 예: 0.05 = 5%, 기본: 0.05)",
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
            print(f"🧩 전략 클래스 로드됨: {name} (파일: {strategy_file})")
            return obj
    
    raise ValueError(f"전략 클래스를 찾을 수 없습니다: {strategy_file}")


def resolve_strategy_path(strategy_file: Path) -> Path:
    if strategy_file.exists():
        return strategy_file
    candidate = (project_root / "scripts/strategies" / strategy_file).resolve()
    if candidate.exists():
        return candidate
    return strategy_file


async def main():
    """메인 함수."""
    args = parse_args()
    args.strategy_file = resolve_strategy_path(args.strategy_file)
    
    # 날짜 파싱
    try:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
        end_date = end_date.replace(hour=23, minute=59, second=59)
    except ValueError as e:
        print(f"❌ 날짜 형식 오류: {e}")
        print("   형식: YYYY-MM-DD (예: 2024-01-01)")
        return
    
    start_ts = int(start_date.timestamp() * 1000)
    end_ts = int(end_date.timestamp() * 1000)
    
    print("=" * 80)
    print("📊 백테스트 설정")
    print("=" * 80)
    print(f"전략 파일: {args.strategy_file}")
    print(f"심볼: {args.symbol}")
    print(f"레버리지: {args.leverage}x")
    print(f"캔들 간격: {args.candle_interval}")
    print(f"최대 포지션: {args.max_position * 100:.1f}%")
    print(f"초기 자산: ${args.initial_balance:,.2f}")
    print(f"기간: {args.start_date} ~ {args.end_date}")
    print(f"수수료율: {args.commission * 100:.4f}%")
    print(f"StopLoss 비율: {args.stop_loss_pct * 100:.1f}%")
    print("=" * 80)
    print()
    
    # 설정 로드
    settings = get_settings()
    
    # 클라이언트 생성 (데이터 조회만 하므로 API 키는 선택사항이지만 기본값 사용)
    # 백테스트는 실서버 데이터 사용 (라이브 트레이딩은 테스트넷 사용)
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key or "",
        api_secret=settings.binance.api_secret or "",
        base_url="https://fapi.binance.com",  # 실서버 URL
    )
    
    try:
        # 과거 데이터 수집
        klines = await fetch_all_klines(
            client=client,
            symbol=args.symbol,
            interval=args.candle_interval,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        
        if not klines:
            print("❌ 데이터가 없습니다.")
            return
        
        print()
        
        # 리스크 관리자 생성
        risk_config = RiskConfig(
            max_leverage=float(args.leverage),
            max_position_size=args.max_position,
            max_order_size=args.max_position,
            stop_loss_pct=args.stop_loss_pct,
        )
        risk_manager = BacktestRiskManager(risk_config)
        
        # 백테스트 컨텍스트 생성
        ctx = BacktestContext(
            symbol=args.symbol,
            leverage=args.leverage,
            initial_balance=args.initial_balance,
            risk_manager=risk_manager,
            commission_rate=args.commission,
        )
        
        # 전략 로드
        strategy_class = load_strategy_class(args.strategy_file)
        # 전략 인스턴스 생성 (전략 파라미터는 전략 코드 내부 기본값 사용)
        strategy = strategy_class()
        
        # 백테스트 엔진 생성 및 실행
        engine = BacktestEngine(strategy, ctx, klines)
        results = engine.run()
        
        # 결과 출력
        print()
        print("=" * 80)
        print("📈 백테스트 결과")
        print("=" * 80)
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
