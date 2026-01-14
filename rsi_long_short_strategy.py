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


class RsiLongShortStrategy(Strategy):
    """RSI 기반 롱/숏 전략.

    목적:
    - RSI 지표를 활용한 양방향 트레이딩 전략

    규칙:
    - 롱 포지션 진입: RSI(기본 14)가 long_entry_rsi 아래에서 long_entry_rsi 상향 돌파 시 진입
    - 롱 포지션 청산(둘 중 먼저 충족):
      - RSI가 long_exit_rsi 상향 돌파 시 청산
      - StopLoss: 현재 미실현 손익(PnL)이 자본금(Balance)의 -5%를 초과할 때 청산
    - 숏 포지션 진입: RSI가 short_entry_rsi 위에서 short_entry_rsi 하향 돌파 시 진입
    - 숏 포지션 청산(둘 중 먼저 충족):
      - RSI가 short_exit_rsi 하향 돌파 시 청산
      - StopLoss: 현재 미실현 손익(PnL)이 자본금(Balance)의 -5%를 초과할 때 청산

    참고:
    - 엔진이 tick마다 on_bar을 호출할 수 있게 run_on_tick=True 로 둠
      - tick에서는 StopLoss만 체크
      - 새 봉(is_new_bar=True)에서만 RSI 크로스 판단/prev_rsi 갱신
    - 롱과 숏 포지션은 동시에 존재할 수 없음 (position_size로 관리)
    """
    # 라이브 엔진이 tick마다 on_bar을 호출하도록 하는 힌트
    run_on_tick = True

    def __init__(
        self,
        # quantity는 더 이상 고정 수량으로 쓰지 않음(자동 포지션 사이징 사용).
        # 다만 너무 작은 값/라운딩으로 0이 되는 것을 방지하기 위해 최소 수량으로 사용.
        quantity: float = 0.001,
        rsi_period: int = 14,
        long_entry_rsi: float = 30.0,
        long_exit_rsi: float = 70.0,
        short_entry_rsi: float = 70.0,
        short_exit_rsi: float = 30.0,
        max_position: float = 1.0,
        sizing_buffer: float = 0.98,
        qty_step: float = 0.001,
    ) -> None:
        super().__init__()
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        if not (0 < long_entry_rsi < long_exit_rsi < 100):
            raise ValueError("invalid long RSI thresholds")
        if not (0 < short_exit_rsi < short_entry_rsi < 100):
            raise ValueError("invalid short RSI thresholds")
        if rsi_period <= 1:
            raise ValueError("rsi_period must be > 1")
        if not (0 < max_position <= 1.0):
            raise ValueError("max_position must be in (0, 1]")
        if not (0 < sizing_buffer <= 1.0):
            raise ValueError("sizing_buffer must be in (0, 1]")
        if qty_step <= 0:
            raise ValueError("qty_step must be > 0")

        self.min_quantity = quantity
        self.rsi_period = rsi_period
        self.long_entry_rsi = long_entry_rsi
        self.long_exit_rsi = long_exit_rsi
        self.short_entry_rsi = short_entry_rsi
        self.short_exit_rsi = short_exit_rsi
        self.max_position = max_position
        self.sizing_buffer = sizing_buffer
        self.qty_step = qty_step
        self.prev_rsi: float | None = None
        self.is_closing: bool = False  # 청산 주문 진행 중 플래그 (중복 청산 방지)

    def initialize(self, ctx: StrategyContext) -> None:
        print(f"🚀 [버전확인] RsiLongShortStrategy v1.0 시작!")
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

        # ===== StopLoss 체크 (롱/숏 모두) =====
        # StopLoss는 "실시간 현재가/PnL" 기준 (tick/봉 모두에서 체크)
        # 레버리지와 무관하게 포지션 진입 시점의 balance 대비 %로 계산
        # 백테스트에서는 설정값을 넘어서는 경우 설정값에 맞는 가격으로 역산하여 체결
        if ctx.position_size != 0 and not self.is_closing:
            # PnL 기반 StopLoss 로직
            # 포지션 진입 시점의 balance를 기준으로 계산하여 레버리지와 무관하게 일정한 기준 적용
            entry_balance = float(getattr(ctx, "position_entry_balance", 0.0) or 0.0)
            unrealized_pnl = float(getattr(ctx, "unrealized_pnl", 0.0) or 0.0)
            
            if entry_balance > 0:
                # 포지션 진입 시점의 balance 대비 손익률 계산
                # 레버리지와 무관하게 일정한 기준 적용
                current_pnl_pct = unrealized_pnl / entry_balance
                
                # 시스템 설정에서 stoploss 비율 가져오기
                risk_manager = getattr(ctx, "risk_manager", None)
                if risk_manager and hasattr(risk_manager, "config"):
                    stop_loss_pct = risk_manager.config.stop_loss_pct
                else:
                    stop_loss_pct = 0.05
                
                # 손실률이 설정된 제한(예: -0.05)보다 더 작으면(더 큰 손실이면) 청산
                if current_pnl_pct <= -stop_loss_pct:
                    self.is_closing = True
                    position_type = "Long" if ctx.position_size > 0 else "Short"
                    reason_msg = f"StopLoss {position_type} (PnL {current_pnl_pct*100:.2f}% of entry balance)"
                    ctx.close_position(reason=reason_msg, use_chase=False)

        # RSI는 "마지막 닫힌 봉 close" 기준이어야 하므로,
        # 새 봉이 확정된 시점(is_new_bar=True)에서만 크로스 판단/prev_rsi 갱신.
        if not bool(bar.get("is_new_bar", True)):
            return

        rsi = float(ctx.get_indicator("rsi", self.rsi_period))

        if self.prev_rsi is None:
            self.prev_rsi = rsi
            return

        # ===== 롱 포지션 청산: RSI long_exit_rsi 상향 돌파 =====
        if ctx.position_size > 0 and not self.is_closing:
            if self.prev_rsi < self.long_exit_rsi <= rsi:
                self.is_closing = True
                reason_msg = f"RSI Exit Long ({self.prev_rsi:.1f} -> {rsi:.1f})"
                ctx.close_position(reason=reason_msg)
                self.prev_rsi = rsi
                return

        # ===== 숏 포지션 청산: RSI short_exit_rsi 하향 돌파 =====
        if ctx.position_size < 0 and not self.is_closing:
            if rsi <= self.short_exit_rsi < self.prev_rsi:
                self.is_closing = True
                reason_msg = f"RSI Exit Short ({self.prev_rsi:.1f} -> {rsi:.1f})"
                ctx.close_position(reason=reason_msg)
                self.prev_rsi = rsi
                return

        # ===== 롱 진입: RSI long_entry_rsi 상향 돌파 =====
        if ctx.position_size == 0:
            if self.prev_rsi < self.long_entry_rsi <= rsi:
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
                    
                    reason_msg = f"Entry Long ({self.prev_rsi:.1f} -> {rsi:.1f})"
                    ctx.buy(qty, reason=reason_msg)
                else:
                    reason_msg = f"Entry Long Fallback ({self.prev_rsi:.1f} -> {rsi:.1f})"
                    ctx.buy(self.min_quantity, reason=reason_msg)

        # ===== 숏 진입: RSI short_entry_rsi 하향 돌파 =====
        if ctx.position_size == 0:
            if rsi <= self.short_entry_rsi < self.prev_rsi:
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
                    
                    reason_msg = f"Entry Short ({self.prev_rsi:.1f} -> {rsi:.1f})"
                    ctx.sell(qty, reason=reason_msg)
                else:
                    reason_msg = f"Entry Short Fallback ({self.prev_rsi:.1f} -> {rsi:.1f})"
                    ctx.sell(self.min_quantity, reason=reason_msg)

        self.prev_rsi = rsi