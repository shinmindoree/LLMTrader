"""Portfolio live trading engine (multi-symbol, multi-interval)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from binance.market_stream import BinanceBookTickerStream
from live.context import LiveContext
from live.indicator_context import CandleStreamIndicatorContext
from live.logger import get_logger
from live.portfolio_context import PortfolioContext, StreamKey
from live.price_feed import PriceFeed
from live.user_stream_hub import UserStreamHub
from strategy.base import Strategy


class StreamBoundStrategyContext:
    """현재 (symbol, interval) 스트림에 바인딩된 StrategyContext.

    - 전략 코드는 심볼을 하드코딩하지 않고, 싱글 모드처럼 ctx.buy()/ctx.get_indicator()를 호출한다.
    - 각 스트림 이벤트마다 ctx가 해당 스트림으로 바인딩되어 전달된다.
    """

    def __init__(self, portfolio_ctx: PortfolioContext, *, symbol: str, interval: str) -> None:
        self._portfolio = portfolio_ctx
        self.symbol = symbol
        self.candle_interval = interval

    @property
    def current_price(self) -> float:
        return self._portfolio.for_symbol(self.symbol).current_price

    @property
    def position_size(self) -> float:
        return self._portfolio.for_symbol(self.symbol).position_size

    @property
    def position_entry_price(self) -> float:
        return self._portfolio.for_symbol(self.symbol).position_entry_price

    @property
    def unrealized_pnl(self) -> float:
        return self._portfolio.for_symbol(self.symbol).unrealized_pnl

    @property
    def balance(self) -> float:
        return self._portfolio.for_symbol(self.symbol).balance

    def buy(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self._portfolio.for_symbol(self.symbol).buy(
            quantity,
            price=price,
            reason=reason,
            exit_reason=exit_reason,
            use_chase=use_chase,
        )

    def sell(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self._portfolio.for_symbol(self.symbol).sell(
            quantity,
            price=price,
            reason=reason,
            exit_reason=exit_reason,
            use_chase=use_chase,
        )

    def close_position(
        self,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self._portfolio.for_symbol(self.symbol).close_position(reason=reason, exit_reason=exit_reason, use_chase=use_chase)

    def calc_entry_quantity(self, entry_pct: float | None = None, price: float | None = None) -> float:
        return self._portfolio.for_symbol(self.symbol).calc_entry_quantity(entry_pct=entry_pct, price=price)

    def enter_long(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        self._portfolio.for_symbol(self.symbol).enter_long(reason=reason, entry_pct=entry_pct)

    def enter_short(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        self._portfolio.for_symbol(self.symbol).enter_short(reason=reason, entry_pct=entry_pct)

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self._portfolio.for_symbol(self.symbol).get_open_orders()

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return self._portfolio.get_indicator(name, *args, symbol=self.symbol, interval=self.candle_interval, **kwargs)

    def register_indicator(self, name: str, func: Any) -> None:
        self._portfolio.register_indicator(name, func)


class PortfolioLiveTradingEngine:
    """멀티 (symbol, interval) 스트림을 하나의 전략에서 처리하는 라이브 엔진."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        portfolio_ctx: PortfolioContext,
        price_feeds: dict[StreamKey, PriceFeed],
        stream_contexts: dict[StreamKey, CandleStreamIndicatorContext],
        trade_contexts: dict[str, LiveContext],
        trade_intervals: dict[str, str],
        user_stream_hub: UserStreamHub,
        log_interval: int | None = None,
    ) -> None:
        self.strategy = strategy
        self.ctx = portfolio_ctx
        self.price_feeds = dict(price_feeds)
        self.stream_contexts = dict(stream_contexts)
        self.trade_contexts = dict(trade_contexts)
        self.trade_intervals = dict(trade_intervals)
        self.user_stream_hub = user_stream_hub
        self.log_interval: int | None = log_interval if log_interval and log_interval > 0 else None

        self._logger = get_logger("llmtrader.live.portfolio")
        self._run_on_tick: bool = bool(getattr(strategy, "run_on_tick", False))
        self._running = False
        self._initialized = False
        self._start_time: float = 0.0
        self._last_log_time: float = 0.0

        self._feed_tasks: list[asyncio.Task[None]] = []
        self._book_ticker_streams: list[BinanceBookTickerStream] = []
        self._book_ticker_tasks: list[asyncio.Task[None]] = []

        self.snapshots: list[dict[str, Any]] = []

    async def start(self) -> None:
        self._start_time = time.time()
        self._last_log_time = time.time()

        # 1) contexts initialize (per tradable symbol)
        for symbol, ctx in self.trade_contexts.items():
            ctx.candle_interval = self.trade_intervals.get(symbol, getattr(ctx, "candle_interval", "1m"))
            await ctx.initialize()
            ctx.attach_user_stream()
            self.user_stream_hub.register_handler(ctx._handle_user_stream_event)  # noqa: SLF001
            self.user_stream_hub.register_disconnect_handler(ctx._on_user_stream_disconnect)  # noqa: SLF001
            self.user_stream_hub.register_reconnect_handler(ctx._on_user_stream_reconnect)  # noqa: SLF001

        await self.user_stream_hub.start()

        # 2) seed OHLCV for all candle streams
        seed_limit = 1000
        for (symbol, interval), feed in self.price_feeds.items():
            history = await feed.fetch_closed_ohlcv(limit=seed_limit)
            if not history:
                raise RuntimeError(f"히스토리 시딩 실패: {symbol}@{interval}")

            stream_ctx = self.stream_contexts[(symbol, interval)]
            for item in history:
                stream_ctx.update_bar(
                    float(item["open"]),
                    float(item["high"]),
                    float(item["low"]),
                    float(item["close"]),
                    float(item.get("volume", 0) or 0),
                )

                trade_interval = self.trade_intervals.get(symbol)
                if trade_interval and trade_interval == interval:
                    trade_ctx = self.trade_contexts.get(symbol)
                    if trade_ctx is not None:
                        trade_ctx.update_bar(
                            float(item["open"]),
                            float(item["high"]),
                            float(item["low"]),
                            float(item["close"]),
                            float(item.get("volume", 0) or 0),
                        )

            self._logger.info(
                "히스토리 시딩 완료",
                symbol=symbol,
                candle_interval=interval,
                bars=len(history),
                first_bar_timestamp=int(history[0]["timestamp"]),
                last_bar_timestamp=int(history[-1]["timestamp"]),
            )

        # 3) strategy init (once)
        if not self._initialized:
            for _sym, ctx in self.trade_contexts.items():
                try:
                    ctx.set_strategy_meta(self.strategy)
                except Exception:  # noqa: BLE001
                    pass
            # initialize는 첫 스트림으로 바인딩된 ctx를 전달(전략 코드 호환)
            first_key = next(iter(self.price_feeds.keys()))
            init_ctx = StreamBoundStrategyContext(self.ctx, symbol=first_key[0], interval=first_key[1])
            self.strategy.initialize(init_ctx)
            self._initialized = True

        # 4) subscribe feeds + bookTicker per tradable symbol
        for _key, feed in self.price_feeds.items():
            feed.subscribe(self._on_price_update)

        # bookTicker: per tradable symbol, to keep Chase Order behavior
        is_testnet = "testnet" in next(iter(self.price_feeds.values())).client.base_url.lower()
        for symbol, ctx in self.trade_contexts.items():
            stream = BinanceBookTickerStream(
                symbol=symbol,
                callback=ctx.update_book_ticker,
                testnet=is_testnet,
            )
            self._book_ticker_streams.append(stream)
            self._book_ticker_tasks.append(asyncio.create_task(stream.start()))

        self._running = True
        for feed in self.price_feeds.values():
            self._feed_tasks.append(asyncio.create_task(feed.start()))

        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            await self.stop_async()

    def stop(self) -> None:
        self._running = False

    async def stop_async(self) -> None:
        self._running = False

        await self.user_stream_hub.stop()

        for feed in self.price_feeds.values():
            await feed.stop()
        for stream in self._book_ticker_streams:
            await stream.stop()

        for task in self._feed_tasks:
            if not task.done():
                task.cancel()
        for task in self._book_ticker_tasks:
            if not task.done():
                task.cancel()

    def _on_price_update(self, tick: dict[str, Any]) -> None:
        symbol = str(tick.get("symbol", "")).upper()
        interval = str(tick.get("interval", "")).strip()
        key: StreamKey = (symbol, interval)

        price = float(tick.get("price", 0))
        is_new_bar = bool(tick.get("is_new_bar", False))

        stream_ctx = self.stream_contexts.get(key)
        if stream_ctx is not None:
            if price > 0:
                stream_ctx.mark_price(price)
            if is_new_bar:
                stream_ctx.update_bar(
                    float(tick.get("bar_open", price)),
                    float(tick.get("bar_high", price)),
                    float(tick.get("bar_low", price)),
                    float(tick.get("bar_close", price)),
                    float(tick.get("volume", 0) or 0),
                )

        trade_interval = self.trade_intervals.get(symbol)
        trade_ctx = self.trade_contexts.get(symbol)
        if trade_ctx is not None:
            if price > 0:
                # interval과 무관하게 최신 가격을 반영(전략 ctx.current_price 호환)
                trade_ctx.mark_price(price)
                trade_ctx.check_stoploss()
            if is_new_bar:
                if trade_interval and interval == trade_interval:
                    trade_ctx.update_bar(
                        float(tick.get("bar_open", price)),
                        float(tick.get("bar_high", price)),
                        float(tick.get("bar_low", price)),
                        float(tick.get("bar_close", price)),
                        float(tick.get("volume", 0) or 0),
                    )
                    trade_ctx.on_new_bar(int(tick.get("bar_timestamp", tick.get("timestamp", 0)) or 0))

        # Strategy dispatch
        if is_new_bar or self._run_on_tick:
            bar_ts = int(tick.get("bar_timestamp", tick.get("timestamp", 0)) or 0)
            if is_new_bar:
                open_price = float(tick.get("bar_open", price) or price)
                high_price = float(tick.get("bar_high", price) or price)
                low_price = float(tick.get("bar_low", price) or price)
                close_price = float(tick.get("bar_close", price) or price)
            else:
                open_price = price
                high_price = price
                low_price = price
                close_price = price
            bar = {
                "symbol": symbol,
                "interval": interval,
                "timestamp": int(tick.get("timestamp", 0) or 0),
                "bar_timestamp": bar_ts,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "bar_open": tick.get("bar_open"),
                "bar_high": tick.get("bar_high"),
                "bar_low": tick.get("bar_low"),
                "bar_close": tick.get("bar_close"),
                "price": price,
                "volume": tick.get("volume", 0),
                "is_new_bar": bool(is_new_bar),
            }
            try:
                bound_ctx = StreamBoundStrategyContext(self.ctx, symbol=symbol, interval=interval)
                self.strategy.on_bar(bound_ctx, bar)
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error(
                    error_type="STRATEGY_ERROR",
                    message=str(exc),
                    symbol=symbol,
                    candle_interval=interval,
                    bar_timestamp=bar_ts,
                )

        # Snapshot/logging (minimal)
        should_log = False
        now_sec = time.time()
        if self.log_interval:
            if now_sec - self._last_log_time >= self.log_interval:
                should_log = True
                self._last_log_time = now_sec
        else:
            if is_new_bar:
                should_log = True

        if should_log:
            self._save_snapshot(tick)

    def _save_snapshot(self, tick: dict[str, Any]) -> None:
        ts = int(tick.get("timestamp", 0) or 0)
        now = datetime.now()
        trigger_stream = f"{str(tick.get('symbol', '')).upper()}@{str(tick.get('interval', '')).strip()}"
        bar_time = now.isoformat(timespec="seconds")
        portfolio_total_equity = self.ctx.portfolio_total_equity()

        # 터미널 출력(로그 인터벌 반영)
        parts: list[str] = []
        for symbol, ctx in self.trade_contexts.items():
            price = float(ctx.current_price)
            pos = float(ctx.position_size)
            pnl = float(ctx.unrealized_pnl)
            parts.append(f"{symbol} price={price:,.2f} pos={pos:+.4f} pnl={pnl:+.2f}")
        self._logger.info(
            f"PORTFOLIO_TICK | time={bar_time} | total_equity={portfolio_total_equity:,.2f} | " + " | ".join(parts),
            trigger_stream=trigger_stream,
        )

        snapshot: dict[str, Any] = {
            "timestamp": ts,
            "datetime": datetime.fromtimestamp(ts / 1000).isoformat(timespec="seconds") if ts else "",
            "portfolio_total_equity": portfolio_total_equity,
            "symbols": {},
        }
        for symbol, ctx in self.trade_contexts.items():
            snapshot["symbols"][symbol] = {
                "price": ctx.current_price,
                "position_size": ctx.position_size,
                "position_entry_price": ctx.position.entry_price,
                "unrealized_pnl": ctx.unrealized_pnl,
                "num_pending_orders": len(ctx.pending_orders),
                "num_filled_orders": len(ctx.filled_orders),
            }
        self.snapshots.append(snapshot)

    def get_summary(self) -> dict[str, Any]:
        if not self.snapshots:
            return {}

        initial_equity = float(self.snapshots[0].get("portfolio_total_equity") or 0)
        final_equity = float(self.snapshots[-1].get("portfolio_total_equity") or 0)
        total_return = (final_equity - initial_equity) / initial_equity if initial_equity > 0 else 0.0
        total_return_pct = total_return * 100.0

        out: dict[str, Any] = {
            "initial_equity": initial_equity,
            "final_equity": final_equity,
            "total_return_pct": total_return_pct,
            "num_snapshots": len(self.snapshots),
            "symbols": {},
        }
        for symbol, ctx in self.trade_contexts.items():
            out["symbols"][symbol] = {
                "position_size": ctx.position_size,
                "position_entry_price": ctx.position.entry_price,
                "unrealized_pnl": ctx.unrealized_pnl,
                "num_filled_orders": len(ctx.filled_orders),
                "num_pending_orders": len(ctx.pending_orders),
            }
        return out
