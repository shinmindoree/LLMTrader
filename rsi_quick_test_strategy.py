from llmtrader.strategy.base import Strategy
from llmtrader.strategy.context import StrategyContext


class RsiQuickTestStrategy(Strategy):
    """빠른 라이브(테스트넷) 검증용 RSI 전략 (거래 빈도 ↑).

    목적:
    - 주문/포지션/청산/손익 흐름이 제대로 동작하는지 빠르게 확인하기 위한 테스트용
    - 검증 완료 후에는 원래 전략 파일로 되돌아갈 것

    동작:
    - 진입: RSI(기본 6) entry_rsi 상향 돌파 시 롱
    - 청산: RSI exit_rsi 상향 돌파 시 청산 OR 진입가 대비 -stop_loss_usd 하락 시 청산

    기본값(거래 빈도 높게):
    - rsi_period=6, entry_rsi=45, exit_rsi=55, stop_loss_usd=150
    """

    def __init__(
        self,
        quantity: float = 0.01,
        rsi_period: int = 6,
        entry_rsi: float = 45.0,
        exit_rsi: float = 55.0,
        stop_loss_usd: float = 150.0,
    ) -> None:
        super().__init__()
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        if not (0 < entry_rsi < exit_rsi < 100):
            raise ValueError("invalid RSI thresholds")
        if rsi_period <= 1:
            raise ValueError("rsi_period must be > 1")
        if stop_loss_usd <= 0:
            raise ValueError("stop_loss_usd must be > 0")

        self.quantity = quantity
        self.rsi_period = rsi_period
        self.entry_rsi = entry_rsi
        self.exit_rsi = exit_rsi
        self.stop_loss_usd = stop_loss_usd

        self.prev_rsi: float | None = None

    def initialize(self, ctx: StrategyContext) -> None:
        self.prev_rsi = None

    def on_bar(self, ctx: StrategyContext, bar: dict) -> None:
        current_rsi = float(ctx.get_indicator("rsi", self.rsi_period))

        if self.prev_rsi is None:
            self.prev_rsi = current_rsi
            return

        # 포지션 관리 (롱만)
        if ctx.position_size > 0:
            # RSI exit 상향 돌파 시 청산
            if self.prev_rsi < self.exit_rsi <= current_rsi:
                ctx.close_position()
                self.prev_rsi = current_rsi
                return

            # 작은 스탑로스로 청산 빈도↑
            entry_price = float(ctx.position_entry_price)
            if entry_price > 0 and ctx.current_price <= entry_price - self.stop_loss_usd:
                ctx.close_position()
                self.prev_rsi = current_rsi
                return

        # 진입 (RSI entry 상향 돌파)
        if ctx.position_size == 0:
            if self.prev_rsi < self.entry_rsi <= current_rsi:
                ctx.buy(self.quantity)

        self.prev_rsi = current_rsi


