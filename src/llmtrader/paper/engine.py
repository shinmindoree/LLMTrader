"""페이퍼 트레이딩 엔진."""

import asyncio
from datetime import datetime
from typing import Any

from llmtrader.paper.context import PaperContext
from llmtrader.paper.price_feed import PriceFeed
from llmtrader.strategy.base import Strategy


class PaperTradingEngine:
    """페이퍼 트레이딩 엔진."""

    def __init__(
        self,
        strategy: Strategy,
        price_feed: PriceFeed,
        initial_balance: float = 10000.0,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        slippage: float = 0.0001,
    ) -> None:
        """페이퍼 트레이딩 엔진 초기화.

        Args:
            strategy: 실행할 전략
            price_feed: 가격 피드
            initial_balance: 초기 잔고
            maker_fee: 메이커 수수료율
            taker_fee: 테이커 수수료율
            slippage: 슬리피지율
        """
        self.strategy = strategy
        self.price_feed = price_feed
        self.ctx = PaperContext(initial_balance, maker_fee, taker_fee, slippage)
        self.snapshots: list[dict[str, Any]] = []
        self._initialized = False
        self._running = False
        self._last_bar_timestamp: int | None = None
        self._current_bar_timestamp: int | None = None
        self._run_on_tick: bool = bool(getattr(strategy, "run_on_tick", False))

    async def start(self) -> None:
        """페이퍼 트레이딩 시작."""
        self._running = True

        # 지표가 50.0에 고정되는 문제 방지: 시작 시 최근 캔들로 price_history 시딩(seed)
        try:
            strat_period = getattr(self.strategy, "rsi_period", 14)
            rsi_period = int(strat_period) if strat_period else 14
        except Exception:  # noqa: BLE001
            rsi_period = 14

        seed_limit = max(200, rsi_period + 50)
        try:
            history = await self.price_feed.fetch_closed_closes(limit=seed_limit)
            if history:
                for _, close in history:
                    self.ctx.update_price(float(close))
                last_bar_ts = int(history[-1][0])
                self._last_bar_timestamp = last_bar_ts
                self._current_bar_timestamp = last_bar_ts
        except Exception:  # noqa: BLE001
            # 시딩 실패해도 페이퍼 트레이딩은 계속 진행
            pass

        # 가격 피드 콜백 등록
        self.price_feed.subscribe(self._on_price_update)

        # 가격 피드 시작 (백그라운드)
        feed_task = asyncio.create_task(self.price_feed.start())

        # 메인 루프
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            self.price_feed.stop()
            try:
                await asyncio.wait_for(feed_task, timeout=2.0)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        """페이퍼 트레이딩 중지."""
        self._running = False

    def _on_price_update(self, tick: dict[str, Any]) -> None:
        """가격 업데이트 시 호출.

        Args:
            tick: 가격 틱 데이터
        """
        # 1) 폴링 주기마다 마크가격 반영 (로그/스탑로스/지정가 체결용)
        price = float(tick["price"])
        self.ctx.mark_price(price)

        # 전략 초기화 (첫 틱)
        if not self._initialized:
            self.strategy.initialize(self.ctx)
            self._initialized = True

        # 2) 새 1분봉이 확정될 때만 지표(price_history) 업데이트 + on_bar 실행
        bar_ts = int(tick.get("bar_timestamp", 0))
        if bar_ts:
            self._current_bar_timestamp = bar_ts
        is_new_bar = bool(tick.get("is_new_bar", False))
        if is_new_bar and (self._last_bar_timestamp != bar_ts):
            bar_close = float(tick.get("bar_close", price))
            self.ctx.update_price(bar_close)
            # update_price()가 current_price를 bar_close로 덮어쓰므로, 최신 마크가격을 다시 반영
            self.ctx.mark_price(price)

            bar = {
                "timestamp": bar_ts,
                "open": bar_close,
                "high": bar_close,
                "low": bar_close,
                "close": bar_close,
                "volume": tick.get("volume", 0),
                "is_new_bar": True,
            }
            self.strategy.on_bar(self.ctx, bar)
            self._last_bar_timestamp = bar_ts
        elif self._run_on_tick:
            # 초고빈도 테스트용: 폴링 주기마다 전략 실행(지표 히스토리는 닫힌 봉에서만 갱신)
            bar = {
                "timestamp": int(tick.get("timestamp", 0)),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": tick.get("volume", 0),
                "is_new_bar": False,
            }
            self.strategy.on_bar(self.ctx, bar)

        # 스냅샷 저장
        # 로그는 폴링 주기마다 찍히도록 "로컬 수신 시각"을 사용
        self._save_snapshot(
            int(tick.get("timestamp", 0)),
            bar_timestamp=self._current_bar_timestamp,
        )

    def _save_snapshot(self, timestamp: int, bar_timestamp: int | None = None) -> None:
        """현재 상태 스냅샷 저장.

        Args:
            timestamp: 타임스탬프
            bar_timestamp: 현재 봉 타임스탬프 (kline open time, ms)
        """
        total_equity = self.ctx.balance + self.ctx.unrealized_pnl

        # RSI 로깅용: 기본 RSI(14) + (전략이 rsi_period를 가지면 그 값)
        rsi14 = float(self.ctx.get_indicator("rsi", 14))
        strat_period = getattr(self.strategy, "rsi_period", 14)
        try:
            strat_period_int = int(strat_period)
        except Exception:  # noqa: BLE001
            strat_period_int = 14
        rsi_strat = float(self.ctx.get_indicator("rsi", strat_period_int))

        snapshot = {
            "timestamp": timestamp,
            # 초 단위까지 표시되도록 timespec 지정
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
            "total_equity": total_equity,
            "num_open_orders": len(self.ctx.open_orders),
            "num_filled_orders": len(self.ctx.filled_orders),
            "rsi14": rsi14,
            "rsi_period": strat_period_int,
            "rsi": rsi_strat,
        }
        self.snapshots.append(snapshot)

        # 콘솔 출력
        rsi_text = (
            f"RSI14: {snapshot['rsi14']:.1f} | RSI{snapshot['rsi_period']}: {snapshot['rsi']:.1f}"
            if snapshot["rsi_period"] != 14
            else f"RSI14: {snapshot['rsi14']:.1f}"
        )
        bar_text = f"(bar={snapshot['bar_datetime']}) " if snapshot["bar_datetime"] else ""
        print(
            f"[{snapshot['datetime']}] {bar_text}"
            f"Price: ${snapshot['price']:.2f} | "
            f"{rsi_text} | "
            f"Position: {snapshot['position_size']:.4f} | "
            f"Balance: ${snapshot['balance']:.2f} | "
            f"PnL: ${snapshot['unrealized_pnl']:.2f} | "
            f"Total: ${snapshot['total_equity']:.2f}"
        )

    def get_summary(self) -> dict[str, Any]:
        """요약 통계 반환.

        Returns:
            요약 통계
        """
        if not self.snapshots:
            return {}

        final_equity = self.snapshots[-1]["total_equity"]
        total_return = (final_equity - self.ctx.initial_balance) / self.ctx.initial_balance
        total_return_pct = total_return * 100

        # MDD 계산
        peak = self.ctx.initial_balance
        max_dd = 0.0
        for snapshot in self.snapshots:
            equity = snapshot["total_equity"]
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        return {
            "initial_balance": self.ctx.initial_balance,
            "final_equity": final_equity,
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd * 100,
            "num_snapshots": len(self.snapshots),
            "num_filled_orders": len(self.ctx.filled_orders),
        }

