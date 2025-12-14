"""커스텀 전략 페이퍼 트레이딩 실행 스크립트."""

import asyncio
import json
import sys
from pathlib import Path

import typer

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.paper.engine import PaperTradingEngine
from llmtrader.paper.price_feed import PriceFeed
from llmtrader.settings import get_settings

app = typer.Typer()


@app.command()
def paper_trade(
    strategy_file: str = typer.Argument(..., help="전략 파일 경로 (예: ma_cross_strategy.py)"),
    symbol: str = typer.Option("BTCUSDT", "--symbol", "-s", help="심볼"),
    balance: float = typer.Option(10000.0, "--balance", "-b", help="초기 잔고"),
    interval: float = typer.Option(1.0, "--interval", "-i", help="가격 피드 간격 (초)"),
    candle_interval: str = typer.Option("1m", "--candle-interval", help="캔들 봉 간격 (예: 1m, 5m, 15m)"),
) -> None:
    """커스텀 전략으로 페이퍼 트레이딩 실행."""
    asyncio.run(_paper_trade_async(strategy_file, symbol, balance, interval, candle_interval))


async def _paper_trade_async(
    strategy_file: str,
    symbol: str,
    balance: float,
    interval: float,
    candle_interval: str,
) -> None:
    """비동기 페이퍼 트레이딩 실행."""
    # 전략 파일 로드
    strategy_path = Path(strategy_file)
    if not strategy_path.exists():
        typer.echo(f"Error: Strategy file not found: {strategy_file}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading strategy from {strategy_file}...")

    # 전략 파일 동적 로드
    import importlib.util

    spec = importlib.util.spec_from_file_location("custom_strategy", strategy_path)
    if not spec or not spec.loader:
        typer.echo("Error: Cannot load strategy file", err=True)
        raise typer.Exit(1)

    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_strategy"] = module
    spec.loader.exec_module(module)

    # Strategy 클래스 찾기
    strategy_class = None
    for name in dir(module):
        obj = getattr(module, name)
        if (
            isinstance(obj, type)
            and name.endswith("Strategy")
            and name != "Strategy"
        ):
            strategy_class = obj
            break

    if not strategy_class:
        typer.echo("Error: No Strategy class found in file", err=True)
        raise typer.Exit(1)

    typer.echo(f"Found strategy: {strategy_class.__name__}")

    # 전략 인스턴스 생성
    try:
        strategy = strategy_class()
    except Exception as e:
        typer.echo(f"Error creating strategy instance: {e}", err=True)
        raise typer.Exit(1)

    # 설정 로드
    settings = get_settings()

    # 바이낸스 클라이언트 생성
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )

    # 가격 피드 생성
    price_feed = PriceFeed(
        client=client,
        symbol=symbol,
        interval=interval,
        candle_interval=candle_interval,
    )

    # 페이퍼 트레이딩 엔진 생성
    engine = PaperTradingEngine(
        strategy=strategy,
        price_feed=price_feed,
        initial_balance=balance,
        maker_fee=0.0002,
        taker_fee=0.0004,
        slippage=0.0001,
    )

    typer.echo(f"\nStarting paper trading with {symbol}... (Press Ctrl+C to stop)")
    typer.echo("=" * 80)

    try:
        # 엔진 시작
        await engine.start()
    except KeyboardInterrupt:
        typer.echo("\n" + "=" * 80)
        typer.echo("Paper trading stopped.")
    finally:
        # 요약 통계 출력
        summary = engine.get_summary()
        typer.echo("\n=== Paper Trading Summary ===")
        typer.echo(json.dumps(summary, indent=2))

        # 클라이언트 종료
        await client.aclose()


if __name__ == "__main__":
    app()

