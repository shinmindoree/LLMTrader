from llmtrader.strategy.base import Strategy
from llmtrader.strategy.context import StrategyContext
from decimal import Decimal, ROUND_DOWN


class RsiUltraQuickTestStrategy(Strategy):
    """테스트용 RSI 롱 전략 (요구사항 버전).

    목적:
    - 단순한 룰로 라이브(테스트넷) 파이프라인이 정상 동작하는지 검증

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
    # 라이브 엔진이 tick마다 on_bar을 호출하도록 하는 힌트
    run_on_tick = True

    def __init__(
        self,
        # quantity는 더 이상 고정 수량으로 쓰지 않음(자동 포지션 사이징 사용).
        # 다만 너무 작은 값/라운딩으로 0이 되는 것을 방지하기 위해 최소 수량으로 사용.
        quantity: float = 0.001,
        rsi_period: int = 3,
        entry_rsi: float = 30.0,
        exit_rsi: float = 70.0,
        stop_loss_usd: float = 50.0,
        max_position: float = 1.0,
        sizing_buffer: float = 0.98,
        qty_step: float = 0.001,
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
        if not (0 < max_position <= 1.0):
            raise ValueError("max_position must be in (0, 1]")
        if not (0 < sizing_buffer <= 1.0):
            raise ValueError("sizing_buffer must be in (0, 1]")
        if qty_step <= 0:
            raise ValueError("qty_step must be > 0")

        self.min_quantity = quantity
        self.rsi_period = rsi_period
        self.entry_rsi = entry_rsi
        self.exit_rsi = exit_rsi
        self.stop_loss_usd = stop_loss_usd
        self.max_position = max_position
        self.sizing_buffer = sizing_buffer
        self.qty_step = qty_step
        self.prev_rsi: float | None = None

    def initialize(self, ctx: StrategyContext) -> None:
        self.prev_rsi = None

    def on_bar(self, ctx: StrategyContext, bar: dict) -> None:
        # ===== 롱 전용 강제(전략 레벨 가드) =====
        # - BUY는 포지션이 0일 때만 허용
        # - SELL(청산)은 포지션이 +일 때만 허용
        #   (실수로 short가 잡힌 경우를 대비해, 포지션<=0에서는 청산 시그널을 무시)

        # StopLoss는 "실시간 현재가" 기준 (tick/봉 모두에서 체크)
        if ctx.position_size > 0:
            entry = float(ctx.position_entry_price)
            if entry > 0 and ctx.current_price <= entry - self.stop_loss_usd:
                # 포지션이 있을 때만 청산(SELL) 허용
                if ctx.position_size > 0:
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
                # 포지션이 있을 때만 청산(SELL) 허용
                if ctx.position_size > 0:
                    ctx.close_position()
                self.prev_rsi = rsi
                return

        # ===== 롱 진입: RSI 30 상향 돌파 =====
        if ctx.position_size == 0:
            if self.prev_rsi < self.entry_rsi <= rsi:
                # 포지션 0일 때만 진입(BUY) 허용 + 자동 포지션 사이징
                if ctx.position_size == 0:
                    leverage = float(getattr(ctx, "leverage", 1.0) or 1.0)
                    equity = float(getattr(ctx, "total_equity", 0.0) or 0.0)
                    price = float(getattr(ctx, "current_price", 0.0) or 0.0)
                    if equity > 0 and price > 0 and leverage > 0:
                        # 사용자가 원하는 동작:
                        # - 레버리지 5x, max_position=1.0 이면
                        #   목표 명목가치(notional) = 총자산 * 5
                        target_notional = equity * leverage * self.max_position * self.sizing_buffer
                        raw_qty = target_notional / price
                        # 거래소 수량 스텝 반영(내림) - float 노이즈 방지(Decimal)
                        dq = (Decimal(str(raw_qty)) / Decimal(str(self.qty_step))).to_integral_value(
                            rounding=ROUND_DOWN
                        ) * Decimal(str(self.qty_step))
                        qty = float(dq)
                        if qty < self.min_quantity:
                            qty = self.min_quantity
                        ctx.buy(qty)
                    else:
                        # 데이터가 부족하면 최소 수량으로 진입(안전 fallback)
                        ctx.buy(self.min_quantity)

        self.prev_rsi = rsi


