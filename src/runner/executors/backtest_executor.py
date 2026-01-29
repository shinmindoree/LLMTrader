from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest.context import BacktestContext
from backtest.data_fetcher import fetch_all_klines
from backtest.engine import BacktestEngine
from backtest.risk import BacktestRiskManager
from binance.client import BinanceHTTPClient
from common.risk import RiskConfig
from control.enums import EventKind
from runner.event_sink import DbEventSink
from runner.strategy_loader import build_strategy, load_strategy_class
from settings import get_settings


async def run_backtest(
    *,
    repo_root: Path,
    strategy_path: str,
    config: dict[str, Any],
    sink: DbEventSink,
    should_stop: asyncio.Event,
) -> dict[str, Any]:
    symbol = str(config.get("symbol") or "BTCUSDT").upper()
    interval = str(config.get("interval") or "1h")
    leverage = int(config.get("leverage") or 1)
    initial_balance = float(config.get("initial_balance") or 1000.0)
    commission = float(config.get("commission") or 0.0004)
    stop_loss_pct = float(config.get("stop_loss_pct") or 0.05)
    start_ts = int(config.get("start_ts") or 0)
    end_ts = int(config.get("end_ts") or 0)
    strategy_params = config.get("strategy_params") or {}

    sink.emit(kind=EventKind.LOG, message="BACKTEST_START", payload={"symbol": symbol, "interval": interval})

    settings = get_settings()
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key or "",
        api_secret=settings.binance.api_secret or "",
        base_url="https://fapi.binance.com",
        timeout=60.0,
    )

    try:
        klines = await fetch_all_klines(
            client=client,
            symbol=symbol,
            interval=interval,
            start_ts=start_ts,
            end_ts=end_ts,
            progress_callback=lambda p: sink.emit(kind=EventKind.PROGRESS, message="DATA_FETCH", payload={"pct": p}),
        )
        if should_stop.is_set():
            return {"stopped": True}

        if not klines:
            raise ValueError("No klines returned for backtest")

        risk_config = RiskConfig(
            max_leverage=float(leverage),
            max_position_size=float(config.get("max_position") or 0.5),
            max_order_size=float(config.get("max_position") or 0.5),
            stop_loss_pct=stop_loss_pct,
        )
        risk_manager = BacktestRiskManager(risk_config)
        ctx = BacktestContext(
            symbol=symbol,
            leverage=leverage,
            initial_balance=initial_balance,
            risk_manager=risk_manager,
            commission_rate=commission,
        )

        strategy_file = (repo_root / strategy_path).resolve()
        strategy_class = load_strategy_class(strategy_file)
        strategy = build_strategy(strategy_class, dict(strategy_params) if isinstance(strategy_params, dict) else {})

        def progress_cb(pct: float) -> None:
            sink.emit_from_thread(kind=EventKind.PROGRESS, message="BACKTEST_PROGRESS", payload={"pct": pct})
            if should_stop.is_set():
                raise RuntimeError("STOP_REQUESTED")

        engine = BacktestEngine(strategy=strategy, context=ctx, klines=klines, progress_callback=progress_cb)
        results = await asyncio.to_thread(engine.run)

        # Attach trades for UI (summary only; large lists can be paged later)
        results["num_trades"] = len(ctx.trades)
        results["finished_at"] = datetime.now().isoformat()
        return results
    finally:
        await client.aclose()

