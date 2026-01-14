"""라이브 트레이딩 엔진."""

import asyncio
import time
from datetime import datetime
from typing import Any

from live.context import LiveContext
from indicators.rsi import rsi_wilder_from_closes
from live.price_feed import PriceFeed
from live.logger import get_logger
from strategy.base import Strategy
from binance.market_stream import BinanceBookTickerStream


class LiveTradingEngine:
    """라이브 트레이딩 엔진."""

    def __init__(
        self,
        strategy: Strategy,
        context: LiveContext,
        price_feed: PriceFeed,
        log_interval: int | None = None,
    ) -> None:
        """라이브 트레이딩 엔진 초기화.

        Args:
            strategy: 실행할 전략
            context: 라이브 트레이딩 컨텍스트
            price_feed: 가격 피드
            log_interval: 로그 출력 주기 (초). None 또는 0이면 캔들 마감 시에만 저장
        """
        self.strategy = strategy
        self.ctx = context
        self.price_feed = price_feed
        self.snapshots: list[dict[str, Any]] = []
        self._initialized = False
        self._running = False
        self._current_bar_timestamp: int | None = None
        self._current_bar_close: float = 0.0
        self._last_bar_timestamp: int | None = None
        self._run_on_tick: bool = bool(getattr(strategy, "run_on_tick", False))
        self._start_time: float = 0.0
        self._logger = get_logger("llmtrader.live")
        self.log_interval: int | None = log_interval if log_interval and log_interval > 0 else None
        self._last_log_time: float = 0.0
        self._book_ticker_stream: BinanceBookTickerStream | None = None
        self._book_ticker_task: asyncio.Task[None] | None = None

    @staticmethod
    def _compute_rsi_from_closes(closes: list[float], period: int = 14) -> float:
        return rsi_wilder_from_closes(list(closes), int(period))

    async def start(self) -> None:
        """라이브 트레이딩 시작."""
        self._start_time = time.time()
        # 로그 시간 초기화 (시작 시점으로 설정)
        self._last_log_time = time.time()

        strategy_name = self.strategy.__class__.__name__
        leverage = getattr(self.ctx, "leverage", 1)
        max_position = getattr(self.ctx.risk_manager.config, "max_position_size", 1.0)
        self._logger.log_session_start(
            symbol=self.price_feed.symbol,
            strategy=strategy_name,
            leverage=int(leverage),
            max_position=max_position,
        )

        self.ctx.candle_interval = self.price_feed.candle_interval
        
        await self.ctx.initialize()

        try:
            await self.ctx.start_user_stream()
        except Exception as e:  # noqa: BLE001
            self._logger.log_error(
                error_type="USER_STREAM_START_FAILED",
                message=str(e),
                symbol=self.price_feed.symbol,
            )
        
        try:
            strat_period = getattr(self.strategy, "rsi_period", 14)
            rsi_period = int(strat_period) if strat_period else 14
        except Exception:  # noqa: BLE001
            rsi_period = 14

        seed_limit = 1000
        try:
            history = await self.price_feed.fetch_closed_closes(limit=seed_limit)
            for _, close in history:
                self.ctx.update_price(float(close))
            if history:
                self._current_bar_timestamp = int(history[-1][0])
                self._last_bar_timestamp = self._current_bar_timestamp
                self._current_bar_close = float(history[-1][1])
        except Exception:  # noqa: BLE001
            pass
        
        self._running = True

        self.price_feed.subscribe(self._on_price_update)

        is_testnet = "testnet" in self.price_feed.client.base_url.lower()
        self._book_ticker_stream = BinanceBookTickerStream(
            symbol=self.price_feed.symbol,
            callback=self.ctx.update_book_ticker,
            testnet=is_testnet,
        )
        self._book_ticker_task = asyncio.create_task(self._book_ticker_stream.start())

        feed_task = asyncio.create_task(self.price_feed.start())

        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            await self.ctx.stop_user_stream()
            await self.price_feed.stop()
            if self._book_ticker_stream:
                await self._book_ticker_stream.stop()
            try:
                await asyncio.wait_for(feed_task, timeout=2.0)
            except asyncio.TimeoutError:
                pass
            if self._book_ticker_task:
                try:
                    await asyncio.wait_for(self._book_ticker_task, timeout=2.0)
                except asyncio.TimeoutError:
                    pass

    def stop(self) -> None:
        """라이브 트레이딩 중지."""
        self._running = False

    def _on_price_update(self, tick: dict[str, Any]) -> None:
        """가격 업데이트 시 호출.

        Args:
            tick: 가격 틱 데이터
        """
        last_price = float(tick["price"])
        self.ctx.mark_price(last_price)

        bar_ts = int(tick.get("bar_timestamp", 0))
        bar_close = float(tick.get("bar_close", self._current_bar_close or last_price))

        if bar_ts:
            if self._current_bar_timestamp is None or bar_ts >= self._current_bar_timestamp:
                self._current_bar_timestamp = bar_ts
                self._current_bar_close = bar_close

        if not self._initialized:
            try:
                self.ctx.set_strategy_meta(self.strategy)
            except Exception:  # noqa: BLE001
                pass
            self.strategy.initialize(self.ctx)
            self._initialized = True

        is_new_bar = bool(tick.get("is_new_bar", False))
        if is_new_bar and bar_ts and (self._last_bar_timestamp != bar_ts):
            self.ctx.update_price(bar_close)
            self.ctx.mark_price(last_price)
            # 새 봉 시작 시 cooldown 업데이트
            self.ctx.on_new_bar(bar_ts)

            bar = {
                "timestamp": bar_ts,
                "open": bar_close,
                "high": bar_close,
                "low": bar_close,
                "close": bar_close,
                "volume": tick.get("volume", 0),
                "is_new_bar": True,
            }
            try:
                self.strategy.on_bar(self.ctx, bar)
            except Exception as e:
                self._logger.log_error(
                    error_type="STRATEGY_ERROR",
                    message=str(e),
                    symbol=self.price_feed.symbol,
                    bar_timestamp=bar_ts,
                )
                self.ctx._log_audit("STRATEGY_ERROR", {"error": str(e)})
            self._last_bar_timestamp = bar_ts
        elif self._run_on_tick:
            bar = {
                "timestamp": int(tick.get("timestamp", 0)),
                "open": last_price,
                "high": last_price,
                "low": last_price,
                "close": last_price,
                "volume": tick.get("volume", 0),
                "is_new_bar": False,
            }
            try:
                self.strategy.on_bar(self.ctx, bar)
            except Exception as e:
                self._logger.log_error(
                    error_type="STRATEGY_ERROR",
                    message=str(e),
                    symbol=self.price_feed.symbol,
                    is_tick=True,
                )
                self.ctx._log_audit("STRATEGY_ERROR", {"error": str(e)})

        should_log = False
        current_ts_sec = time.time()

        if self.log_interval:
            if current_ts_sec - self._last_log_time >= self.log_interval:
                should_log = True
                self._last_log_time = current_ts_sec
        else:
            if is_new_bar and bar_ts and (self._last_bar_timestamp == bar_ts):
                should_log = True

        if should_log:
            asyncio.create_task(self._update_account_and_save_snapshot(tick["timestamp"], bar_ts))

    async def _update_account_and_save_snapshot(self, timestamp: int, bar_timestamp: int) -> None:
        """계좌 정보 업데이트 후 스냅샷 저장.
        
        Args:
            timestamp: 타임스탬프
            bar_timestamp: 현재 봉 타임스탬프
        """
        try:
            await self.ctx.update_account_info()
        except Exception as e:
            self._logger.log_error(
                error_type="ACCOUNT_UPDATE_ERROR",
                message=f"Failed to update account info: {e}",
                symbol=self.price_feed.symbol,
            )
        
        self._save_snapshot(timestamp, bar_timestamp=bar_timestamp)

    def _save_snapshot(self, timestamp: int, bar_timestamp: int | None = None) -> None:
        """현재 상태 스냅샷 저장.

        Args:
            timestamp: 타임스탬프
            bar_timestamp: 현재 봉 타임스탬프 (kline open time, ms)
        """
        closes = list(self.ctx._price_history)
    
        strat_period = getattr(self.strategy, "rsi_period", 14)
        try:
            strat_period_int = int(strat_period)
        except Exception:  # noqa: BLE001
            strat_period_int = 14
        rsi_p = self._compute_rsi_from_closes(closes, period=strat_period_int)
        rsi_rt_p = self._compute_rsi_from_closes(closes + [float(self.ctx.current_price)], period=strat_period_int)

        snapshot = {
            "timestamp": timestamp,
            "datetime": datetime.fromtimestamp(timestamp / 1000).isoformat(timespec="seconds"),
            "bar_timestamp": bar_timestamp or 0,
            "bar_datetime": (
                datetime.fromtimestamp((bar_timestamp or 0) / 1000).isoformat(timespec="minutes")
                if bar_timestamp
                else ""
            ),
            "price": self.ctx.current_price,
            "balance": self.ctx.balance,
            "position_size": self.ctx.position_size,
            "position_entry_price": self.ctx.position.entry_price,
            "unrealized_pnl": self.ctx.unrealized_pnl,
            "total_equity": self.ctx.total_equity,
            "num_pending_orders": len(self.ctx.pending_orders),
            "num_filled_orders": len(self.ctx.filled_orders),
            "bar_close": self._current_bar_close,
            "rsi_period": strat_period_int,
            "rsi_p": rsi_p,
            "rsi_rt_p": rsi_rt_p,
        }
        self.snapshots.append(snapshot)

        self._logger.log_tick(
            symbol=self.price_feed.symbol,
            bar_time=snapshot["bar_datetime"],
            price=snapshot["price"],
            rsi=snapshot["rsi_p"],
            rsi_rt=snapshot["rsi_rt_p"],
            position=snapshot["position_size"],
            balance=snapshot["balance"],
            pnl=snapshot["unrealized_pnl"],
            rsi_period=snapshot["rsi_period"],
            bar_close=snapshot["bar_close"],
            total_equity=snapshot["total_equity"],
        )

    def get_summary(self) -> dict[str, Any]:
        """요약 통계 반환.

        Returns:
            요약 통계
        """
        if not self.snapshots:
            return {}

        initial_equity = self.snapshots[0]["total_equity"]
        final_equity = self.snapshots[-1]["total_equity"]
        total_return = (final_equity - initial_equity) / initial_equity if initial_equity > 0 else 0
        total_return_pct = total_return * 100

        peak = initial_equity
        max_dd = 0.0
        for snapshot in self.snapshots:
            equity = snapshot["total_equity"]
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        risk_status = self.ctx.risk_manager.get_status()

        num_trades = len(self.ctx.filled_orders)
        wins = sum(1 for o in self.ctx.filled_orders if o.get("realized_pnl", 0) > 0)
        win_rate = wins / num_trades if num_trades > 0 else 0.0
        duration_minutes = (time.time() - self._start_time) / 60 if self._start_time else 0.0

        self._logger.log_session_end(
            symbol=self.price_feed.symbol,
            total_trades=num_trades,
            total_pnl=final_equity - initial_equity,
            win_rate=win_rate,
            duration_minutes=duration_minutes,
            initial_equity=initial_equity,
            final_equity=final_equity,
            max_drawdown_pct=max_dd * 100,
        )

        return {
            "initial_equity": initial_equity,
            "final_equity": final_equity,
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd * 100,
            "num_snapshots": len(self.snapshots),
            "num_filled_orders": num_trades,
            "risk_status": risk_status,
            "audit_log_size": len(self.ctx.audit_log),
        }
