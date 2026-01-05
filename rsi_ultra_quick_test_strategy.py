import sys
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

# src 디렉토리를 Python 경로에 추가
project_root = Path(__file__).parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from strategy.base import Strategy
from strategy.context import StrategyContext


class RsiUltraQuickTestStrategy(Strategy):
    """테스트용 RSI 롱 전략 (요구사항 버전).

    목적:
    - 단순한 룰로 라이브(테스트넷) 파이프라인이 정상 동작하는지 검증

    규칙:
    - 포지션 진입: LONG만
      - RSI(기본 14) 가 30 아래에서 30 상향 돌파 시 진입
    - 포지션 청산(둘 중 먼저 충족):
      - RSI 가 70 상향 돌파 시 청산 (RSI는 "마지막 닫힌 봉 close" 기준)
      - StopLoss: 현재 미실현 손익(PnL)이 자본금(Balance)의 -5%를 초과할 때 청산

    참고:
    - 엔진이 tick마다 on_bar을 호출할 수 있게 run_on_tick=True 로 둠
      - tick에서는 StopLoss만 체크
      - 새 봉(is_new_bar=True)에서만 RSI 크로스 판단/prev_rsi 갱신
    """
    # 라이브 엔진이 tick마다 on_bar을 호출하도록 하는 힌트
    run_on_tick = True

    def __init__(
        self,
        quantity: float = 0.001,
        rsi_period: int = 14,
        entry_rsi: float = 30.0,
        exit_rsi: float = 70.0,
        stop_loss_pct: float = 0.05,
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
        self.stop_loss_pct = stop_loss_pct
        self.max_position = max_position
        self.sizing_buffer = sizing_buffer
        self.qty_step = qty_step
        self.prev_rsi: float | None = None
        self.is_closing: bool = False

    def initialize(self, ctx: StrategyContext) -> None:
        print(f"🚀 RsiUltraQuickStrategy 시작!")
        self.prev_rsi = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        open_orders = getattr(ctx, "get_open_orders", lambda: [])()
        if open_orders:
            return

        if ctx.position_size > 0 and not self.is_closing:
            # 레버리지와 무관하게 포지션 진입 시점의 balance 대비 %로 계산
            # 백테스트에서는 설정값을 넘어서는 경우 설정값에 맞는 가격으로 역산하여 체결
            entry_balance = float(getattr(ctx, "position_entry_balance", 0.0) or 0.0)
            unrealized_pnl = float(getattr(ctx, "unrealized_pnl", 0.0) or 0.0)
            
            if entry_balance > 0:
                # 포지션 진입 시점의 balance 대비 손익률 계산
                current_pnl_pct = unrealized_pnl / entry_balance
                
                if current_pnl_pct <= -self.stop_loss_pct:
                    self.is_closing = True
                    
                    # 설정값에 정확히 맞는 가격 역산 (롱 포지션만)
                    entry_price = ctx.position_entry_price
                    position_size = abs(ctx.position_size)
                    
                    # stop_loss_pct = -(target_price - entry_price) * size / entry_balance
                    # target_price = entry_price - (stop_loss_pct * entry_balance / size)
                    target_price = entry_price - (self.stop_loss_pct * entry_balance / position_size)
                    
                    # 가격이 유효한 범위 내인지 확인 (음수 방지)
                    if target_price > 0:
                        reason_msg = f"StopLoss (PnL {(-self.stop_loss_pct)*100:.2f}% of entry balance)"
                        ctx.close_position_at_price(target_price, reason=reason_msg)
                    else:
                        # 가격이 유효하지 않으면 현재가로 청산
                        reason_msg = f"StopLoss (PnL {current_pnl_pct*100:.2f}% of entry balance)"
                        ctx.close_position(reason=reason_msg)

        if not bool(bar.get("is_new_bar", True)):
            return

        rsi = float(ctx.get_indicator("rsi", self.rsi_period))

        if self.prev_rsi is None:
            self.prev_rsi = rsi
            return

        if ctx.position_size > 0 and not self.is_closing:
            if self.prev_rsi < self.exit_rsi <= rsi:
                if ctx.position_size > 0:
                    self.is_closing = True
                    reason_msg = f"RSI Exit ({self.prev_rsi:.1f} -> {rsi:.1f})"
                    ctx.close_position(reason=reason_msg)
                self.prev_rsi = rsi
                return

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