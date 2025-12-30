from llmtrader.strategy.base import Strategy
from llmtrader.strategy.context import StrategyContext


class Rsi30LongWithStopStrategy(Strategy):
    """BTCUSDT Perp 1분봉 기준 데모 전략.

    - 진입: RSI(14) 30 상향 돌파 시 롱 진입
    - 청산: (1) RSI(14) 70 상향 돌파 시 전량 청산
            (2) 진입가 대비 -$500 하락 시 전량 청산 (Stop Loss)
      -> 둘 중 먼저 만족하는 조건으로 청산
    """

    def __init__(
        self,
        quantity: float = 0.01,
        rsi_period: int = 14,
        entry_rsi: float = 30.0,
        exit_rsi: float = 70.0,
        stop_loss_usd: float = 500.0,
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

        # 첫 RSI 값이면 저장만
        if self.prev_rsi is None:
            self.prev_rsi = current_rsi
            return

        # ====== 포지션 관리 (롱만) ======
        if ctx.position_size > 0:
            # 1) RSI 70 상향 돌파 청산
            if self.prev_rsi < self.exit_rsi <= current_rsi:
                ctx.close_position()
                self.prev_rsi = current_rsi
                return

            # 2) 스탑로스: 진입가 대비 -$500 하락 시 청산
            entry_price = float(ctx.position_entry_price)
            if entry_price > 0 and ctx.current_price <= entry_price - self.stop_loss_usd:
                ctx.close_position()
                self.prev_rsi = current_rsi
                return

        # ====== 진입 ======
        if ctx.position_size == 0:
            # RSI 30 상향 돌파 시 롱 진입
            if self.prev_rsi < self.entry_rsi <= current_rsi:
                ctx.buy(self.quantity)

        self.prev_rsi = current_rsi






