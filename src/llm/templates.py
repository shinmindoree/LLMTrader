"""전략 코드 템플릿."""


def get_strategy_template(class_name: str = "GeneratedStrategy") -> str:
    """전략 코드 기본 템플릿.

    Args:
        class_name: 전략 클래스 이름

    Returns:
        전략 코드 템플릿
    """
    return f'''import sys
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

# src 디렉토리를 Python 경로에 추가
project_root = Path(__file__).parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from strategy.base import Strategy
from strategy.context import StrategyContext


class {class_name}(Strategy):
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
{{init_params}}
        
        # 상태 변수 (필수)
        self.prev_rsi: float | None = None
        self.is_closing: bool = False  # 중복 청산 방지
    
    def initialize(self, ctx: StrategyContext) -> None:
        """전략 초기화."""
        self.prev_rsi = None
        self.is_closing = False
        self.current_daily_loss = 0.0
        self.consecutive_loss_count = 0
    
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
        {{stop_loss_logic}}
        
        # ===== 4. 지표 시그널은 새 봉에서만 =====
        if not bool(bar.get("is_new_bar", True)):
            return
        
{{trading_logic}}
        
        # ===== 5. prev_rsi 갱신 (필요한 경우) =====
{{prev_indicator_update}}
'''


def get_safe_patterns() -> dict[str, str]:
    """안전 패턴 템플릿.

    Returns:
        안전 패턴 딕셔너리
    """
    return {
        "duplicate_order_guard": """
        # 미체결 주문 가드
        open_orders = getattr(ctx, "get_open_orders", lambda: [])()
        if open_orders:
            return
        """,
        "duplicate_close_guard": """
        # 중복 청산 방지
        if ctx.position_size != 0 and not self.is_closing:
            # 청산 로직
            self.is_closing = True
            ctx.close_position(reason="...")
        """,
        "position_check_buy": """
        # 롱 진입: 포지션 없을 때만
        if ctx.position_size == 0:
            ctx.buy(qty, reason="Entry Long")
        """,
        "position_check_sell": """
        # 숏 진입: 포지션 없을 때만
        if ctx.position_size == 0:
            ctx.sell(qty, reason="Entry Short")
        """,
        "position_check_close_long": """
        # 롱 청산: 롱 포지션 있을 때만
        if ctx.position_size > 0:
            ctx.close_position(reason="Exit Long")
        """,
        "position_check_close_short": """
        # 숏 청산: 숏 포지션 있을 때만
        if ctx.position_size < 0:
            ctx.close_position(reason="Exit Short")
        """,
        "stop_loss_pattern": """
        # StopLoss 체크
        if ctx.position_size != 0 and not self.is_closing:
            entry_balance = float(getattr(ctx, "position_entry_balance", 0.0) or 0.0)
            unrealized_pnl = float(getattr(ctx, "unrealized_pnl", 0.0) or 0.0)
            
            if entry_balance > 0:
                pnl_pct = unrealized_pnl / entry_balance
                if pnl_pct <= -self.stop_loss_pct:
                    self.is_closing = True
                    position_type = "Long" if ctx.position_size > 0 else "Short"
                    ctx.close_position(reason=f"StopLoss {{position_type}}")
        """,
        "position_sizing": """
        # 자동 포지션 사이징
        leverage = float(getattr(ctx, "leverage", 1.0) or 1.0)
        equity = float(getattr(ctx, "total_equity", 0.0) or 0.0)
        price = float(getattr(ctx, "current_price", 0.0) or 0.0)
        
        if equity > 0 and price > 0:
            target_notional = equity * leverage * self.max_position * 0.98
            raw_qty = target_notional / price
            
            dq = (Decimal(str(raw_qty)) / Decimal(str(self.qty_step))).to_integral_value(
                rounding=ROUND_DOWN
            ) * Decimal(str(self.qty_step))
            qty = float(dq)
            
            if qty >= self.min_quantity:
                ctx.buy(qty, reason="...")
        """,
        "new_bar_check": """
        # 새 봉에서만 지표 크로스 판단
        if not bool(bar.get("is_new_bar", True)):
            return
        """,
    }


def format_template(template: str, **kwargs) -> str:
    """템플릿 포맷팅.

    Args:
        template: 템플릿 문자열
        **kwargs: 포맷팅 인자

    Returns:
        포맷팅된 문자열
    """
    return template.format(**kwargs)
