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
      - StopLoss: 현재 미실현 손익(PnL)이 총 자산(Equity)의 -5%를 초과할 때 청산

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
        rsi_period: int = 14,
        entry_rsi: float = 30.0,
        exit_rsi: float = 70.0,
        stop_loss_pct: float = 0.05,  # [변경] 5% 손실 기준 (0.05)
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
        # [변경] 퍼센트 유효성 검사 (0.0 ~ 1.0 사이)
        if not (0 < stop_loss_pct < 1.0):
            raise ValueError("stop_loss_pct must be between 0 and 1 (e.g. 0.05 for 5%)")
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
        self.stop_loss_pct = stop_loss_pct  # [변경] USD -> PCT
        self.max_position = max_position
        self.sizing_buffer = sizing_buffer
        self.qty_step = qty_step
        self.prev_rsi: float | None = None
        self.is_closing: bool = False  # 청산 주문 진행 중 플래그 (중복 청산 방지)

    def initialize(self, ctx: StrategyContext) -> None:
        self.prev_rsi = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict) -> None:
        # ===== 청산 플래그 리셋 =====
        if ctx.position_size == 0:
            self.is_closing = False

        # ===== 미체결 주문 가드 =====
        open_orders = getattr(ctx, "get_open_orders", lambda: [])()
        if open_orders:
            return

        # ===== 롱 전용 강제 및 StopLoss 체크 =====
        # StopLoss는 "실시간 현재가/PnL" 기준 (tick/봉 모두에서 체크)
        if ctx.position_size > 0 and not self.is_closing:
            # [변경] PnL 기반 StopLoss 로직
            # equity = balance + unrealized_pnl (현재 총 자산가치)
            equity = float(getattr(ctx, "total_equity", 0.0) or 0.0)
            unrealized_pnl = float(getattr(ctx, "unrealized_pnl", 0.0) or 0.0)
            
            if equity > 0:
                # 현재 손익률 계산 (예: -50불 / 1000불 = -0.05)
                current_pnl_pct = unrealized_pnl / equity
                
                # 손실률이 설정된 제한(예: -0.05)보다 더 작으면(더 큰 손실이면) 청산
                if current_pnl_pct <= -self.stop_loss_pct:
                    self.is_closing = True
                    # [변경] 로그 사유에 PnL 정보 포함
                    reason_msg = f"StopLoss (PnL {current_pnl_pct*100:.2f}%)"
                    ctx.close_position(reason=reason_msg)

        # RSI는 "마지막 닫힌 봉 close" 기준이어야 하므로,
        # 새 봉이 확정된 시점(is_new_bar=True)에서만 크로스 판단/prev_rsi 갱신.
        if not bool(bar.get("is_new_bar", True)):
            return

        rsi = float(ctx.get_indicator("rsi", self.rsi_period))

        if self.prev_rsi is None:
            self.prev_rsi = rsi
            return

        # ===== 롱 청산: RSI 70 상향 돌파 =====
        if ctx.position_size > 0 and not self.is_closing:
            if self.prev_rsi < self.exit_rsi <= rsi:
                if ctx.position_size > 0:
                    self.is_closing = True
                    reason_msg = f"RSI Exit ({self.prev_rsi:.1f} -> {rsi:.1f})"
                    ctx.close_position(reason=reason_msg)
                self.prev_rsi = rsi
                return

        # ===== 롱 진입: RSI 30 상향 돌파 =====
        if ctx.position_size == 0:
            if self.prev_rsi < self.entry_rsi <= rsi:
                if ctx.position_size == 0:
                    leverage = float(getattr(ctx, "leverage", 1.0) or 1.0)
                    equity = float(getattr(ctx, "total_equity", 0.0) or 0.0)
                    price = float(getattr(ctx, "current_price", 0.0) or 0.0)
                    if equity > 0 and price > 0 and leverage > 0:
                        target_notional = equity * leverage * self.max_position * self.sizing_buffer
                        raw_qty = target_notional / price
                        dq = (Decimal(str(raw_qty)) / Decimal(str(self.qty_step))).to_integral_value(
                            rounding=ROUND_DOWN
                        ) * Decimal(str(self.qty_step))
                        qty = float(dq)
                        if qty < self.min_quantity:
                            qty = self.min_quantity
                        
                        reason_msg = f"Entry ({self.prev_rsi:.1f} -> {rsi:.1f})"
                        ctx.buy(qty, reason=reason_msg)
                    else:
                        reason_msg = f"Entry Fallback ({self.prev_rsi:.1f} -> {rsi:.1f})"
                        ctx.buy(self.min_quantity, reason=reason_msg)

        self.prev_rsi = rsi