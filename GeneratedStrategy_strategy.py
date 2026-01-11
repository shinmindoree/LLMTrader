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


class GeneratedStrategy(Strategy):
    """
    LLM이 생성한 전략.
    
    주의: 이 코드는 자동 생성되었습니다. 실행 전에 반드시 검증하세요.
    """
    # 라이브 엔진이 tick마다 on_bar을 호출하도록 하는 힌트
    run_on_tick = True
    
    def __init__(
        self,
        # 파라미터는 전략에 따라 추가
        **kwargs
    ) -> None:
        super().__init__()
        # 파라미터 초기화
        self.stop_loss_pct = 0.05
        self.max_position = 0.1
        self.min_quantity = 0.001
        
        # 상태 변수 (필수)
        self.prev_rsi: float | None = None
        self.is_closing: bool = False  # 중복 청산 방지
    
    def initialize(self, ctx: StrategyContext) -> None:
        """전략 초기화."""
        self.prev_rsi = None
        self.is_closing = False
    
    def on_bar(self, ctx: StrategyContext, bar: dict) -> None:
        """
        매 봉/틱마다 호출.
        
        bar 구조:
        - bar["is_new_bar"]: bool - 새 봉 확정 여부
        - bar["bar_close"]: float - 닫힌 봉의 종가
        - bar["price"]: float - 실시간 가격
        """
        # ===== 1. 청산 플래그 리셋 =====
        if ctx.position_size == 0:
            self.is_closing = False
        
        # ===== 2. 미체결 주문 가드 (중복 주문 방지) =====
        open_orders = getattr(ctx, "get_open_orders", lambda: [])()
        if open_orders:
            return
        
        # ===== 3. StopLoss 체크 (tick마다 실행) =====
                # StopLoss 체크
        if ctx.position_size != 0 and not self.is_closing:
            entry_balance = float(getattr(ctx, "position_entry_balance", 0.0) or 0.0)
            unrealized_pnl = float(getattr(ctx, "unrealized_pnl", 0.0) or 0.0)
            
            if entry_balance > 0:
                pnl_pct = unrealized_pnl / entry_balance
                if pnl_pct <= -self.stop_loss_pct:
                    self.is_closing = True
                    position_type = "Long" if ctx.position_size > 0 else "Short"
                    ctx.close_position(reason=f"StopLoss {position_type}")
        
        # ===== 4. 지표 시그널은 새 봉에서만 =====
        if not bool(bar.get("is_new_bar", True)):
            return
        
        # 지표 계산

        # 트레이딩 로직
        
        # ===== 5. prev_rsi 갱신 (필요한 경우) =====
        # 이전 지표 값 갱신
