"""라이브 트레이딩 엔진."""

import asyncio
from datetime import datetime
from typing import Any

from llmtrader.live.context import LiveContext
from llmtrader.indicators.rsi import rsi_wilder_from_closes
from llmtrader.paper.price_feed import PriceFeed
from llmtrader.strategy.base import Strategy


class LiveTradingEngine:
    """라이브 트레이딩 엔진."""

    def __init__(
        self,
        strategy: Strategy,
        context: LiveContext,
        price_feed: PriceFeed,
    ) -> None:
        """라이브 트레이딩 엔진 초기화.

        Args:
            strategy: 실행할 전략
            context: 라이브 트레이딩 컨텍스트
            price_feed: 가격 피드
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

    @staticmethod
    def _compute_rsi_from_closes(closes: list[float], period: int = 14) -> float:
        return rsi_wilder_from_closes(list(closes), int(period))

    async def start(self) -> None:
        """라이브 트레이딩 시작."""
        # 컨텍스트 초기화 (레버리지 설정, 잔고 조회)
        await self.ctx.initialize()
        
        # 지표가 시작부터 의미 있게 나오도록 최근 캔들 종가로 시딩(seed)
        try:
            strat_period = getattr(self.strategy, "rsi_period", 14)
            rsi_period = int(strat_period) if strat_period else 14
        except Exception:  # noqa: BLE001
            rsi_period = 14

        seed_limit = max(200, rsi_period + 50)
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
        """라이브 트레이딩 중지."""
        self._running = False

    def _on_price_update(self, tick: dict[str, Any]) -> None:
        """가격 업데이트 시 호출.

        Args:
            tick: 가격 틱 데이터
        """
        # 현재가(last)는 매 폴링마다 반영 (지표 히스토리는 건드리지 않음)
        last_price = float(tick["price"])
        self.ctx.mark_price(last_price)

        # bar 정보(닫힌 1분봉) 갱신
        bar_ts = int(tick.get("bar_timestamp", 0))
        bar_close = float(tick.get("bar_close", self._current_bar_close or last_price))

        if bar_ts:
            # bar timestamp가 과거로 되돌아가는 경우 무시(노드/캐시 흔들림 방지)
            if self._current_bar_timestamp is None or bar_ts >= self._current_bar_timestamp:
                self._current_bar_timestamp = bar_ts
                self._current_bar_close = bar_close

        # 전략 초기화 (첫 틱)
        if not self._initialized:
            # 주문 체결 로그/알림에 "전략이 쓰는 RSI 파라미터"를 포함하기 위해 메타 주입
            try:
                self.ctx.set_strategy_meta(self.strategy)
            except Exception:  # noqa: BLE001
                pass
            self.strategy.initialize(self.ctx)
            self._initialized = True

        # 새 1분봉이 확정될 때만 on_bar 실행 + 지표 히스토리 업데이트
        is_new_bar = bool(tick.get("is_new_bar", False))
        if is_new_bar and bar_ts and (self._last_bar_timestamp != bar_ts):
            self.ctx.update_price(bar_close)
            # update_price가 current_price를 bar_close로 덮을 수 있으니 다시 last 반영
            self.ctx.mark_price(last_price)

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
                print(f"⚠️ 전략 실행 오류: {e}")
                self.ctx._log_audit("STRATEGY_ERROR", {"error": str(e)})
            self._last_bar_timestamp = bar_ts
        elif self._run_on_tick:
            # 초고빈도 테스트용: 폴링 주기마다 전략 실행(지표 히스토리는 닫힌 봉에서만 갱신)
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
                print(f"⚠️ 전략 실행 오류: {e}")
                self.ctx._log_audit("STRATEGY_ERROR", {"error": str(e)})

        # 스냅샷 저장
        self._save_snapshot(tick["timestamp"], bar_timestamp=self._current_bar_timestamp)

    def _save_snapshot(self, timestamp: int, bar_timestamp: int | None = None) -> None:
        """현재 상태 스냅샷 저장.

        Args:
            timestamp: 타임스탬프
            bar_timestamp: 현재 봉 타임스탬프 (kline open time, ms)
        """
        # check_realtime_btcusdt_rsi.py 와 동일한 의미:
        # - rsi(14): 닫힌 1분봉 close 기준 RSI
        # - rsi_rt(14): 닫힌 close들 + 현재가(last)를 마지막 값으로 반영한 RSI
        closes = list(self.ctx._price_history)
        rsi14 = self._compute_rsi_from_closes(closes, period=14)
        rsi_rt14 = self._compute_rsi_from_closes(closes + [float(self.ctx.current_price)], period=14)

        # 전략의 rsi_period(예: 3)도 함께 출력
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
            "rsi14": rsi14,
            "rsi_rt14": rsi_rt14,
            "bar_close": self._current_bar_close,
            "rsi_period": strat_period_int,
            "rsi_p": rsi_p,
            "rsi_rt_p": rsi_rt_p,
        }
        self.snapshots.append(snapshot)

        # 콘솔 출력: check_realtime_btcusdt_rsi.py 와 동일한 포맷(앞부분)
        # + 라이브 트레이딩 상태(포지션/밸런스)는 뒤에 추가로 붙임
        bar_dt = snapshot["bar_datetime"]
        prefix = (
            f"[{snapshot['datetime']}] "
            f"(bar={bar_dt}) "
            f"last={snapshot['price']:,.2f} "
            f"rsi(14)={snapshot['rsi14']:.2f} "
            f"rsi_rt(14)={snapshot['rsi_rt14']:.2f} "
        )
        if snapshot["rsi_period"] != 14:
            prefix += (
                f"rsi({snapshot['rsi_period']})={snapshot['rsi_p']:.2f} "
                f"rsi_rt({snapshot['rsi_period']})={snapshot['rsi_rt_p']:.2f} "
            )
        prefix += f"bar_close={snapshot['bar_close']:,.2f}"
        position_str = f"{snapshot['position_size']:+.4f}" if snapshot["position_size"] != 0 else "  0.0000"
        pnl_str = f"{snapshot['unrealized_pnl']:+.2f}" if snapshot["unrealized_pnl"] != 0 else "  0.00"
        suffix = (
            f" | Position={position_str} "
            f"| Balance={snapshot['balance']:,.2f} "
            f"| PnL={pnl_str} "
            f"| Total={snapshot['total_equity']:,.2f}"
        )
        print(prefix + suffix)

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

        # MDD 계산
        peak = initial_equity
        max_dd = 0.0
        for snapshot in self.snapshots:
            equity = snapshot["total_equity"]
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # 리스크 관리 상태
        risk_status = self.ctx.risk_manager.get_status()

        return {
            "initial_equity": initial_equity,
            "final_equity": final_equity,
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd * 100,
            "num_snapshots": len(self.snapshots),
            "num_filled_orders": len(self.ctx.filled_orders),
            "risk_status": risk_status,
            "audit_log_size": len(self.ctx.audit_log),
        }

