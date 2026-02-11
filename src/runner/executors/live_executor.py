from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from binance.client import BinanceHTTPClient
from common.risk import RiskConfig
from control.enums import EventKind
from live.context import LiveContext
from live.indicator_context import CandleStreamIndicatorContext
from live.portfolio_context import PortfolioContext
from live.portfolio_engine import PortfolioLiveTradingEngine
from live.price_feed import PriceFeed
from live.risk import LiveRiskManager
from live.user_stream_hub import UserStreamHub
from notifications.slack import SlackNotifier
from runner.event_sink import DbEventSink
from runner.strategy_loader import build_strategy, load_strategy_class
from settings import get_settings


def _parse_interval_seconds(interval: str) -> int:
    s = interval.strip().lower()
    if not s:
        return 0
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    if s.endswith("w"):
        return int(s[:-1]) * 7 * 86400
    return 0


async def _resolve_binance_client(
    user_id: str,
    session_maker: Any,
) -> BinanceHTTPClient:
    """사용자별 암호화된 키를 복호화하여 BinanceHTTPClient를 생성한다."""
    from control.repo import get_user_profile

    async with session_maker() as session:
        profile = await get_user_profile(session, user_id=user_id)

    if profile and profile.binance_api_key_enc and profile.binance_api_secret_enc:
        from common.crypto import get_crypto_service
        crypto = get_crypto_service()
        api_key = crypto.decrypt(profile.binance_api_key_enc)
        api_secret = crypto.decrypt(profile.binance_api_secret_enc)
        base_url = profile.binance_base_url or "https://testnet.binancefuture.com"
        return BinanceHTTPClient(api_key=api_key, api_secret=api_secret, base_url=base_url)

    raise ValueError(
        f"No Binance API keys configured for user {user_id}. "
        "Please configure your keys in Settings before starting a live trade."
    )


async def run_live(
    *,
    repo_root: Path,
    strategy_path: str,
    config: dict[str, Any],
    sink: DbEventSink,
    should_stop: asyncio.Event,
    user_id: str = "legacy",
    session_maker: Any = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not session_maker:
        raise ValueError("session_maker is required for live trading")
    client = await _resolve_binance_client(user_id, session_maker)
    notifier = SlackNotifier(settings.slack.webhook_url) if settings.slack.webhook_url else None

    strategy_file = (repo_root / strategy_path).resolve()
    strategy_class = load_strategy_class(strategy_file)
    strategy_params = config.get("strategy_params") or {}
    strategy = build_strategy(strategy_class, dict(strategy_params) if isinstance(strategy_params, dict) else {})

    streams = config.get("streams") or []
    if not isinstance(streams, list) or not streams:
        raise ValueError("LIVE requires config.streams (list)")

    stream_configs: list[dict[str, Any]] = [s for s in streams if isinstance(s, dict)]
    normalized_streams: list[tuple[str, str]] = []
    for s in stream_configs:
        sym = str(s.get("symbol") or "").upper()
        itv = str(s.get("interval") or "")
        if sym and itv:
            normalized_streams.append((sym, itv))
    if not normalized_streams:
        raise ValueError("No valid streams")

    symbols = sorted({sym for sym, _ in normalized_streams})
    indicator_config = config.get("indicator_config") or {}
    log_interval = config.get("log_interval")
    log_interval_value = int(log_interval) if log_interval is not None else None

    # conservative portfolio risk defaults
    def _min_float(key: str, default: float) -> float:
        vals = [float(s.get(key, default)) for s in stream_configs]
        return min(vals) if vals else default

    def _max_float(key: str, default: float) -> float:
        vals = [float(s.get(key, default)) for s in stream_configs]
        return max(vals) if vals else default

    portfolio_risk_config = RiskConfig(
        max_leverage=_max_float("leverage", 1.0),
        max_position_size=_max_float("max_position", 0.5),
        max_order_size=_max_float("max_position", 0.5),
        daily_loss_limit=_min_float("daily_loss_limit", 500.0),
        max_consecutive_losses=int(_min_float("max_consecutive_losses", 0)),
        stoploss_cooldown_candles=int(_max_float("stoploss_cooldown_candles", 0)),
        stop_loss_pct=_max_float("stop_loss_pct", 0.05),
    )
    portfolio_risk_manager = LiveRiskManager(portfolio_risk_config)

    trade_contexts: dict[str, LiveContext] = {}
    for sym in symbols:
        # pick first stream config for the symbol
        s = next((x for x in stream_configs if str(x.get("symbol", "")).upper() == sym), {})
        leverage = int(s.get("leverage", 1))
        max_position = float(s.get("max_position", 0.5))
        daily_loss_limit = float(s.get("daily_loss_limit", 500.0))
        max_consecutive_losses = int(s.get("max_consecutive_losses", 0))
        stoploss_cooldown_candles = int(s.get("stoploss_cooldown_candles", 0))
        stop_loss_pct = float(s.get("stop_loss_pct", 0.05))

        symbol_risk_config = RiskConfig(
            max_leverage=float(leverage),
            max_position_size=max_position,
            max_order_size=max_position,
            daily_loss_limit=daily_loss_limit,
            max_consecutive_losses=max_consecutive_losses,
            stoploss_cooldown_candles=stoploss_cooldown_candles,
            stop_loss_pct=stop_loss_pct,
        )
        symbol_risk_manager = LiveRiskManager(symbol_risk_config)
        ctx = LiveContext(
            client=client,
            risk_manager=symbol_risk_manager,
            symbol=sym,
            leverage=leverage,
            env=settings.env,
            notifier=notifier,
            indicator_config=indicator_config if isinstance(indicator_config, dict) else None,
            risk_reporter=portfolio_risk_manager.record_trade,
            audit_hook=sink.audit_hook,
        )
        trade_contexts[sym] = ctx

    stream_contexts: dict[tuple[str, str], CandleStreamIndicatorContext] = {}
    price_feeds: dict[tuple[str, str], PriceFeed] = {}
    for sym, itv in normalized_streams:
        key = (sym, itv)
        stream_contexts[key] = CandleStreamIndicatorContext(symbol=sym, interval=itv)
        price_feeds[key] = PriceFeed(client, sym, candle_interval=itv)

    trade_intervals: dict[str, str] = {}
    for sym in symbols:
        intervals = [itv for s, itv in normalized_streams if s == sym]
        intervals_sorted = sorted(intervals, key=_parse_interval_seconds)
        trade_intervals[sym] = intervals_sorted[0] if intervals_sorted else intervals[0]

    user_stream_hub = UserStreamHub(client)
    primary_symbol = normalized_streams[0][0]
    portfolio_ctx = PortfolioContext(
        primary_symbol=primary_symbol,
        trade_contexts=trade_contexts,
        stream_contexts=stream_contexts,
        portfolio_risk_manager=portfolio_risk_manager,
        portfolio_multiplier=float(max(1, len(symbols))),
    )
    engine: Any = PortfolioLiveTradingEngine(
        strategy=strategy,
        portfolio_ctx=portfolio_ctx,
        price_feeds=price_feeds,
        stream_contexts=stream_contexts,
        trade_contexts=trade_contexts,
        trade_intervals=trade_intervals,
        user_stream_hub=user_stream_hub,
        log_interval=log_interval_value,
    )

    sink.emit(kind=EventKind.LOG, message="LIVE_START", payload={"streams": normalized_streams})

    async def stop_watcher() -> None:
        while True:
            if should_stop.is_set():
                sink.emit(kind=EventKind.STATUS, message="STOPPING")
                engine.stop()
                return
            await asyncio.sleep(0.5)

    watcher_task = asyncio.create_task(stop_watcher(), name="live-stop-watcher")
    try:
        await engine.start()
        return {"summary": engine.get_summary()}
    finally:
        watcher_task.cancel()
        await client.aclose()

