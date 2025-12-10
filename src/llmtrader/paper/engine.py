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

    async def start(self) -> None:
        """페이퍼 트레이딩 시작."""
        self._running = True

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
        price = tick["price"]
        self.ctx.update_price(price)

        # 전략 초기화 (첫 틱)
        if not self._initialized:
            self.strategy.initialize(self.ctx)
            self._initialized = True

        # 전략 실행 (on_bar 호출)
        bar = {
            "timestamp": tick["timestamp"],
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": tick.get("volume", 0),
        }
        self.strategy.on_bar(self.ctx, bar)

        # 스냅샷 저장
        self._save_snapshot(tick["timestamp"])

    def _save_snapshot(self, timestamp: int) -> None:
        """현재 상태 스냅샷 저장.

        Args:
            timestamp: 타임스탬프
        """
        total_equity = self.ctx.balance + self.ctx.unrealized_pnl
        snapshot = {
            "timestamp": timestamp,
            "datetime": datetime.fromtimestamp(timestamp / 1000).isoformat(),
            "price": self.ctx.current_price,
            "balance": self.ctx.balance,
            "position_size": self.ctx.position_size,
            "position_entry_price": self.ctx.position.entry_price,
            "unrealized_pnl": self.ctx.unrealized_pnl,
            "total_equity": total_equity,
            "num_open_orders": len(self.ctx.open_orders),
            "num_filled_orders": len(self.ctx.filled_orders),
        }
        self.snapshots.append(snapshot)

        # 콘솔 출력
        print(
            f"[{snapshot['datetime']}] "
            f"Price: ${snapshot['price']:.2f} | "
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

