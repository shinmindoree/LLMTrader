"""리스크 관리 모듈."""

from datetime import datetime, timedelta
from typing import Any


class RiskConfig:
    """리스크 관리 설정."""

    def __init__(
        self,
        max_leverage: float = 10.0,
        max_position_size: float = 1.0,
        daily_loss_limit: float = 1000.0,
        cooldown_after_loss: int = 300,  # 손실 후 쿨다운 (초)
        max_consecutive_losses: int = 3,
        max_order_size: float = 0.5,
    ) -> None:
        """리스크 설정 초기화.

        Args:
            max_leverage: 최대 레버리지 배수
            max_position_size: 최대 포지션 크기 (자산 대비)
            daily_loss_limit: 일일 손실 한도 (USDT)
            cooldown_after_loss: 손실 후 쿨다운 시간 (초)
            max_consecutive_losses: 최대 연속 손실 횟수
            max_order_size: 단일 주문 최대 크기 (자산 대비)
        """
        self.max_leverage = max_leverage
        self.max_position_size = max_position_size
        self.daily_loss_limit = daily_loss_limit
        self.cooldown_after_loss = cooldown_after_loss
        self.max_consecutive_losses = max_consecutive_losses
        self.max_order_size = max_order_size


class RiskManager:
    """리스크 관리자."""

    def __init__(self, config: RiskConfig) -> None:
        """리스크 관리자 초기화.

        Args:
            config: 리스크 설정
        """
        self.config = config
        self._daily_pnl: float = 0.0
        self._daily_reset_time: datetime | None = None
        self._consecutive_losses: int = 0
        self._last_loss_time: datetime | None = None
        self._trade_history: list[dict[str, Any]] = []

    def can_trade(self, current_time: datetime | None = None) -> tuple[bool, str]:
        """거래 가능 여부 확인.

        Args:
            current_time: 현재 시간 (None이면 datetime.now())

        Returns:
            (거래 가능 여부, 사유)
        """
        if current_time is None:
            current_time = datetime.now()

        # 일일 손실 한도 확인
        self._reset_daily_pnl_if_needed(current_time)
        if self._daily_pnl <= -self.config.daily_loss_limit:
            return False, f"일일 손실 한도 도달 (${-self._daily_pnl:.2f})"

        # 쿨다운 확인
        if self._last_loss_time:
            cooldown_end = self._last_loss_time + timedelta(seconds=self.config.cooldown_after_loss)
            if current_time < cooldown_end:
                remaining = (cooldown_end - current_time).total_seconds()
                return False, f"쿨다운 중 (남은 시간: {int(remaining)}초)"

        # 연속 손실 확인
        if self._consecutive_losses >= self.config.max_consecutive_losses:
            return False, f"최대 연속 손실 횟수 도달 ({self._consecutive_losses}회)"

        return True, "OK"

    def validate_order_size(
        self,
        order_size: float,
        current_price: float,
        total_equity: float,
    ) -> tuple[bool, str]:
        """주문 크기 검증.

        Args:
            order_size: 주문 크기 (수량)
            current_price: 현재 가격
            total_equity: 총 자산

        Returns:
            (유효 여부, 사유)
        """
        order_value = order_size * current_price
        max_order_value = total_equity * self.config.max_order_size

        if order_value > max_order_value:
            return False, f"주문 크기 초과 (최대: ${max_order_value:.2f})"

        return True, "OK"

    def validate_position_size(
        self,
        new_position_size: float,
        current_price: float,
        total_equity: float,
    ) -> tuple[bool, str]:
        """포지션 크기 검증.

        Args:
            new_position_size: 새 포지션 크기 (수량)
            current_price: 현재 가격
            total_equity: 총 자산

        Returns:
            (유효 여부, 사유)
        """
        position_value = abs(new_position_size) * current_price
        max_position_value = total_equity * self.config.max_position_size

        if position_value > max_position_value:
            return False, f"포지션 크기 초과 (최대: ${max_position_value:.2f})"

        return True, "OK"

    def validate_leverage(self, leverage: float) -> tuple[bool, str]:
        """레버리지 검증.

        Args:
            leverage: 레버리지 배수

        Returns:
            (유효 여부, 사유)
        """
        if leverage > self.config.max_leverage:
            return False, f"레버리지 초과 (최대: {self.config.max_leverage}x)"

        return True, "OK"

    def record_trade(self, pnl: float, current_time: datetime | None = None) -> None:
        """거래 기록.

        Args:
            pnl: 손익
            current_time: 현재 시간 (None이면 datetime.now())
        """
        if current_time is None:
            current_time = datetime.now()

        self._reset_daily_pnl_if_needed(current_time)
        self._daily_pnl += pnl

        # 연속 손실 추적
        if pnl < 0:
            self._consecutive_losses += 1
            self._last_loss_time = current_time
        else:
            self._consecutive_losses = 0
            self._last_loss_time = None

        # 거래 기록
        self._trade_history.append(
            {
                "timestamp": current_time.isoformat(),
                "pnl": pnl,
                "daily_pnl": self._daily_pnl,
                "consecutive_losses": self._consecutive_losses,
            }
        )

    def _reset_daily_pnl_if_needed(self, current_time: datetime) -> None:
        """필요시 일일 손익 리셋.

        Args:
            current_time: 현재 시간
        """
        if self._daily_reset_time is None:
            self._daily_reset_time = current_time
            return

        # 날짜가 바뀌었으면 리셋
        if current_time.date() > self._daily_reset_time.date():
            self._daily_pnl = 0.0
            self._daily_reset_time = current_time
            self._consecutive_losses = 0
            self._last_loss_time = None

    def get_status(self) -> dict[str, Any]:
        """리스크 관리 상태 반환.

        Returns:
            상태 정보
        """
        return {
            "daily_pnl": self._daily_pnl,
            "daily_loss_limit": self.config.daily_loss_limit,
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive_losses": self.config.max_consecutive_losses,
            "is_in_cooldown": self._last_loss_time is not None,
            "last_loss_time": self._last_loss_time.isoformat() if self._last_loss_time else None,
            "num_trades_today": len([t for t in self._trade_history if datetime.fromisoformat(t["timestamp"]).date() == datetime.now().date()]),
        }
