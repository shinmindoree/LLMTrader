"""라이브 트레이딩용 리스크 관리자."""

from datetime import datetime, timedelta
from typing import Any

from common.risk import BaseRiskManager, RiskConfig


class LiveRiskManager(BaseRiskManager):
    """라이브 트레이딩용 리스크 관리자.

    모든 리스크 관리 기능을 포함합니다:
    - 기본 검증 로직 (상속)
    - 시간 기반 로직 (일일 손실 한도, 쿨다운, 연속 손실)
    """

    def __init__(self, config: RiskConfig) -> None:
        """라이브 리스크 관리자 초기화.

        Args:
            config: 리스크 설정
        """
        super().__init__(config)
        self._daily_pnl: float = 0.0
        self._daily_reset_time: datetime | None = None
        self._consecutive_losses: int = 0
        self._last_loss_time: datetime | None = None
        self._trade_history: list[dict[str, Any]] = []

    def can_trade(self, current_time: datetime | None = None) -> tuple[bool, str]:
        """거래 가능 여부 확인 (라이브 전용).

        Args:
            current_time: 현재 시간 (None이면 datetime.now())

        Returns:
            (거래 가능 여부, 사유)
        """
        if current_time is None:
            current_time = datetime.now()

        self._reset_daily_pnl_if_needed(current_time)
        if self._daily_pnl <= -self.config.daily_loss_limit:
            return False, f"일일 손실 한도 도달 (${-self._daily_pnl:.2f})"

        if self._last_loss_time:
            cooldown_end = self._last_loss_time + timedelta(seconds=self.config.cooldown_after_loss)
            if current_time < cooldown_end:
                remaining = (cooldown_end - current_time).total_seconds()
                return False, f"쿨다운 중 (남은 시간: {int(remaining)}초)"

        if self.config.max_consecutive_losses > 0 and self._consecutive_losses >= self.config.max_consecutive_losses:
            return False, f"최대 연속 손실 횟수 도달 ({self._consecutive_losses}회)"

        return True, "OK"

    def record_trade(self, pnl: float, current_time: datetime | None = None) -> None:
        """거래 기록 (라이브 전용).

        Args:
            pnl: 손익
            current_time: 현재 시간 (None이면 datetime.now())
        """
        if current_time is None:
            current_time = datetime.now()

        self._reset_daily_pnl_if_needed(current_time)
        self._daily_pnl += pnl

        if pnl < 0:
            self._consecutive_losses += 1
            self._last_loss_time = current_time
        else:
            self._consecutive_losses = 0
            self._last_loss_time = None

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
