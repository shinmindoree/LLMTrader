"""커스텀 전략 백테스트 실행 스크립트."""

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import typer

from llmtrader.backtest.data_loader import HistoricalDataLoader
from llmtrader.backtest.engine import BacktestEngine
from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings

app = typer.Typer()


@app.command()
def backtest(
    strategy_file: str = typer.Argument(..., help="전략 파일 경로 (예: ma_cross_strategy.py)"),
    days: int = typer.Option(7, "--days", "-d", help="백테스트 기간 (일)"),
    interval: str = typer.Option("1h", "--interval", "-i", help="캔들 간격 (1m, 5m, 1h, 1d)"),
    symbol: str = typer.Option("BTCUSDT", "--symbol", "-s", help="심볼"),
    balance: float = typer.Option(10000.0, "--balance", "-b", help="초기 잔고"),
) -> None:
    """커스텀 전략 백테스트 실행."""
    asyncio.run(_backtest_async(strategy_file, days, interval, symbol, balance))


async def _backtest_async(
    strategy_file: str,
    days: int,
    interval: str,
    symbol: str,
    balance: float,
) -> None:
    """비동기 백테스트 실행."""
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

    # 데이터 로더
    loader = HistoricalDataLoader(client)

    # 히스토리컬 데이터 로딩
    typer.echo(f"\nLoading {days} days of {symbol} {interval} data...")
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=days)
    klines = await loader.load_klines(
        symbol=symbol,
        interval=interval,
        start_time=start_time,
        end_time=end_time,
    )
    typer.echo(f"Loaded {len(klines)} candles")

    if len(klines) < 10:
        typer.echo("Warning: Not enough data for backtest", err=True)
        await client.aclose()
        raise typer.Exit(1)

    # 백테스트 실행
    typer.echo("\nRunning backtest...")
    engine = BacktestEngine(
        strategy=strategy,
        initial_balance=balance,
        maker_fee=0.0002,
        taker_fee=0.0004,
        slippage=0.0001,
    )
    result = engine.run(klines)

    # 결과 출력
    typer.echo("\n" + "=" * 60)
    typer.echo("BACKTEST RESULTS")
    typer.echo("=" * 60)
    typer.echo(json.dumps(result, indent=2))
    typer.echo("=" * 60)

    # 클라이언트 종료
    await client.aclose()


if __name__ == "__main__":
    app()




