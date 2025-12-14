from llmtrader.strategy.base import Strategy
from llmtrader.strategy.context import StrategyContext


class RsiUltraQuickTestStrategy(Strategy):
    """테스트용 RSI 롱 전략 (요구사항 버전).

    목적:
    - 단순한 룰로 라이브/페이퍼 파이프라인이 정상 동작하는지 검증

    규칙:
    - 포지션 진입: LONG만
      - RSI(기본 14) 가 30 아래에서 30 상향 돌파 시 진입
    - 포지션 청산(둘 중 먼저 충족):
      - RSI 가 70 상향 돌파 시 청산 (RSI는 "마지막 닫힌 봉 close" 기준)
      - StopLoss: 진입가 대비 -80 하락 시 청산 (StopLoss는 "실시간 현재가" 기준)

    참고:
    - 엔진이 tick마다 on_bar을 호출할 수 있게 run_on_tick=True 로 둠
      - tick에서는 StopLoss만 체크
      - 새 봉(is_new_bar=True)에서만 RSI 크로스 판단/prev_rsi 갱신
    """
    # 페이퍼/라이브 엔진이 tick마다 on_bar을 호출하도록 하는 힌트
    run_on_tick = True

    def __init__(
        self,
        quantity: float = 0.01,
        rsi_period: int = 3,
        entry_rsi: float = 30.0,
        exit_rsi: float = 70.0,
        stop_loss_usd: float = 80.0,
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
        # StopLoss는 "실시간 현재가" 기준 (tick/봉 모두에서 체크)
        if ctx.position_size > 0:
            entry = float(ctx.position_entry_price)
            if entry > 0 and ctx.current_price <= entry - self.stop_loss_usd:
                ctx.close_position()
                # 청산 후에도 prev_rsi는 유지(봉에서만 갱신)

        # RSI는 "마지막 닫힌 봉 close" 기준이어야 하므로,
        # 새 봉이 확정된 시점(is_new_bar=True)에서만 크로스 판단/prev_rsi 갱신.
        if not bool(bar.get("is_new_bar", True)):
            return

        rsi = float(ctx.get_indicator("rsi", self.rsi_period))

        if self.prev_rsi is None:
            self.prev_rsi = rsi
            return

        # ===== 롱 청산: RSI 70 상향 돌파 =====
        if ctx.position_size > 0:
            if self.prev_rsi < self.exit_rsi <= rsi:
                ctx.close_position()
                self.prev_rsi = rsi
                return

        # ===== 롱 진입: RSI 30 상향 돌파 =====
        if ctx.position_size == 0:
            if self.prev_rsi < self.entry_rsi <= rsi:
                ctx.buy(self.quantity)

        self.prev_rsi = rsi


