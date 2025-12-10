"""백테스트 엔진."""

from typing import Any

from llmtrader.backtest.report import generate_full_report
from llmtrader.strategy.base import Strategy


class Position:
    """포지션 정보."""

    def __init__(self) -> None:
        """포지션 초기화."""
        self.size: float = 0.0  # 양수: 롱, 음수: 숏
        self.entry_price: float = 0.0
        self.unrealized_pnl: float = 0.0


class BacktestContext:
    """백테스트 컨텍스트 구현."""

    def __init__(
        self,
        initial_balance: float,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        slippage: float = 0.0001,
    ) -> None:
        """컨텍스트 초기화.

        Args:
            initial_balance: 초기 잔고
            maker_fee: 메이커 수수료율 (기본 0.02%)
            taker_fee: 테이커 수수료율 (기본 0.04%)
            slippage: 슬리피지율 (기본 0.01%)
        """
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage = slippage
        self.position = Position()
        self._current_price: float = 0.0
        self._price_history: list[float] = []
        self._indicators: dict[str, Any] = {}

    @property
    def current_price(self) -> float:
        """현재 가격."""
        return self._current_price

    @property
    def position_size(self) -> float:
        """현재 포지션 크기."""
        return self.position.size

    @property
    def unrealized_pnl(self) -> float:
        """미실현 손익."""
        if self.position.size == 0:
            return 0.0
        return self.position.size * (self._current_price - self.position.entry_price)

    def buy(self, quantity: float, price: float | None = None) -> None:
        """매수 주문 (시장가).

        Args:
            quantity: 수량
            price: 가격 (None이면 현재가)
        """
        exec_price = price if price is not None else self._current_price
        # 슬리피지 반영 (매수는 약간 높게)
        exec_price *= 1 + self.slippage
        cost = quantity * exec_price
        fee = cost * self.taker_fee

        # 포지션 업데이트
        if self.position.size < 0:
            # 숏 포지션 청산 후 롱 진입
            realized_pnl = -self.position.size * (self.position.entry_price - exec_price)
            self.balance += realized_pnl
            self.position.size = 0
            self.position.entry_price = 0

        new_size = self.position.size + quantity
        if self.position.size == 0:
            self.position.entry_price = exec_price
        else:
            # 평균 진입가 재계산
            total_cost = self.position.size * self.position.entry_price + cost
            self.position.entry_price = total_cost / new_size

        self.position.size = new_size
        self.balance -= cost + fee

    def sell(self, quantity: float, price: float | None = None) -> None:
        """매도 주문 (시장가).

        Args:
            quantity: 수량
            price: 가격 (None이면 현재가)
        """
        exec_price = price if price is not None else self._current_price
        # 슬리피지 반영 (매도는 약간 낮게)
        exec_price *= 1 - self.slippage
        proceeds = quantity * exec_price
        fee = proceeds * self.taker_fee

        # 포지션 업데이트
        if self.position.size > 0:
            # 롱 포지션 청산
            if quantity >= self.position.size:
                realized_pnl = self.position.size * (exec_price - self.position.entry_price)
                self.balance += realized_pnl + self.position.size * exec_price - fee
                remaining = quantity - self.position.size
                self.position.size = 0
                self.position.entry_price = 0
                # 남은 수량으로 숏 진입
                if remaining > 0:
                    self.position.size = -remaining
                    self.position.entry_price = exec_price
                    self.balance -= remaining * exec_price + fee
            else:
                realized_pnl = quantity * (exec_price - self.position.entry_price)
                self.balance += realized_pnl + proceeds - fee
                self.position.size -= quantity
        else:
            # 숏 포지션 확대
            new_size = self.position.size - quantity
            if self.position.size == 0:
                self.position.entry_price = exec_price
            else:
                total_proceeds = -self.position.size * self.position.entry_price + proceeds
                self.position.entry_price = total_proceeds / abs(new_size)
            self.position.size = new_size
            self.balance += proceeds - fee

    def close_position(self) -> None:
        """현재 포지션 전체 청산."""
        if self.position.size > 0:
            self.sell(self.position.size)
        elif self.position.size < 0:
            self.buy(abs(self.position.size))

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """지표 조회 (임시: 단순 이동평균만 지원).

        Args:
            name: 지표 이름 (예: 'sma')
            *args: 위치 인자 (예: period)
            **kwargs: 키워드 인자

        Returns:
            지표 값
        """
        if name == "sma":
            period = args[0] if args else kwargs.get("period", 20)
            if len(self._price_history) < period:
                return self._current_price
            return sum(self._price_history[-period:]) / period
        return 0.0

    def update_price(self, price: float) -> None:
        """현재 가격 업데이트 (내부용).

        Args:
            price: 새 가격
        """
        self._current_price = price
        self._price_history.append(price)
        # 메모리 절약: 최대 1000개만 유지
        if len(self._price_history) > 1000:
            self._price_history = self._price_history[-1000:]


class BacktestEngine:
    """백테스트 엔진."""

    def __init__(
        self,
        strategy: Strategy,
        initial_balance: float = 10000.0,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        slippage: float = 0.0001,
    ) -> None:
        """백테스트 엔진 초기화.

        Args:
            strategy: 실행할 전략
            initial_balance: 초기 잔고
            maker_fee: 메이커 수수료율
            taker_fee: 테이커 수수료율
            slippage: 슬리피지율
        """
        self.strategy = strategy
        self.ctx = BacktestContext(initial_balance, maker_fee, taker_fee, slippage)
        self.trades: list[dict[str, Any]] = []
        self.equity_curve: list[dict[str, Any]] = []

    def run(self, klines: list[dict[str, Any]]) -> dict[str, Any]:
        """백테스트 실행.

        Args:
            klines: 히스토리컬 캔들 데이터

        Returns:
            백테스트 결과
        """
        # 전략 초기화
        if klines:
            self.ctx.update_price(klines[0]["close"])
        self.strategy.initialize(self.ctx)

        # 시뮬레이션 루프
        for bar in klines:
            self.ctx.update_price(bar["close"])
            self.strategy.on_bar(self.ctx, bar)

            # 에쿼티 커브 기록
            total_equity = self.ctx.balance + self.ctx.unrealized_pnl
            self.equity_curve.append(
                {
                    "timestamp": bar["timestamp"],
                    "balance": self.ctx.balance,
                    "unrealized_pnl": self.ctx.unrealized_pnl,
                    "total_equity": total_equity,
                    "position_size": self.ctx.position_size,
                }
            )

        # 종료 시 포지션 청산
        if self.ctx.position_size != 0:
            self.ctx.close_position()

        # 전략 종료
        self.strategy.finalize(self.ctx)

        # 리포트 생성
        summary = self._generate_summary()
        return generate_full_report(summary, self.equity_curve)

    def _generate_summary(self) -> dict[str, Any]:
        """백테스트 요약 생성.

        Returns:
            요약 통계
        """
        if not self.equity_curve:
            return {}

        final_equity = self.equity_curve[-1]["total_equity"]
        total_return = (final_equity - self.ctx.initial_balance) / self.ctx.initial_balance
        total_return_pct = total_return * 100

        # MDD 계산
        peak = self.ctx.initial_balance
        max_dd = 0.0
        for point in self.equity_curve:
            equity = point["total_equity"]
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
            "num_bars": len(self.equity_curve),
        }

