"""공통 리스크 관리 모듈."""

from typing import Any


class RiskConfig:
    """리스크 관리 설정 (공통)."""

    def __init__(
        self,
        max_leverage: float = 10.0,
        max_position_size: float = 1.0,
        daily_loss_limit: float = 1000.0,
        cooldown_after_loss: int = 300,
        max_consecutive_losses: int = 3,
        max_order_size: float = 0.5,
        stoploss_cooldown_candles: int = 0,
        stop_loss_pct: float = 0.05,
    ) -> None:
        """리스크 설정 초기화.

        Args:
            max_leverage: 최대 레버리지 배수
            max_position_size: 최대 포지션 크기 (자산 대비)
            daily_loss_limit: 일일 손실 한도 (USDT)
            cooldown_after_loss: 손실 후 쿨다운 시간 (초)
            max_consecutive_losses: 최대 연속 손실 횟수
            max_order_size: 단일 주문 최대 크기 (자산 대비)
            stoploss_cooldown_candles: StopLoss 청산 후 거래 중단 캔들 수 (0이면 비활성화)
            stop_loss_pct: StopLoss 비율 (0.0~1.0, 예: 0.05 = 5%)
        """
        self.max_leverage = max_leverage
        self.max_position_size = max_position_size
        self.daily_loss_limit = daily_loss_limit
        self.cooldown_after_loss = cooldown_after_loss
        self.max_consecutive_losses = max_consecutive_losses
        self.max_order_size = max_order_size
        self.stoploss_cooldown_candles = stoploss_cooldown_candles
        self.stop_loss_pct = stop_loss_pct


class BaseRiskManager:
    """기본 리스크 관리자 (공통 검증 로직).

    백테스트와 라이브 트레이딩이 공유하는 기본 검증 로직을 제공합니다.
    시간 기반 로직(can_trade, record_trade)은 하위 클래스에서 구현합니다.
    """

    def __init__(self, config: RiskConfig) -> None:
        """리스크 관리자 초기화.

        Args:
            config: 리스크 설정
        """
        self.config = config

    def validate_order_size(
        self,
        order_size: float,
        current_price: float,
        total_equity: float,
        leverage: float = 1.0,
    ) -> tuple[bool, str]:
        """주문 크기 검증 (공통 로직).

        Args:
            order_size: 주문 크기 (수량)
            current_price: 현재 가격
            total_equity: 총 자산
            leverage: 레버리지

        Returns:
            (유효 여부, 사유)
        """
        if total_equity <= 0:
            return False, f"총자산이 0 이하입니다 (total_equity={total_equity:.2f})"

        order_value = order_size * current_price
        max_order_value = total_equity * leverage * self.config.max_order_size

        if order_value > max_order_value:
            return False, f"주문 크기 초과 (최대: ${max_order_value:.2f})"

        return True, "OK"

    def validate_position_size(
        self,
        new_position_size: float,
        current_price: float,
        total_equity: float,
        leverage: float = 1.0,
    ) -> tuple[bool, str]:
        """포지션 크기 검증 (공통 로직).

        Args:
            new_position_size: 새 포지션 크기 (수량)
            current_price: 현재 가격
            total_equity: 총 자산
            leverage: 레버리지

        Returns:
            (유효 여부, 사유)
        """
        if total_equity <= 0:
            return False, f"총자산이 0 이하입니다 (total_equity={total_equity:.2f})"

        position_value = abs(new_position_size) * current_price
        max_position_value = total_equity * leverage * self.config.max_position_size

        if position_value > max_position_value:
            return False, f"포지션 크기 초과 (최대: ${max_position_value:.2f})"

        return True, "OK"

    def validate_leverage(self, leverage: float) -> tuple[bool, str]:
        """레버리지 검증 (공통 로직).

        Args:
            leverage: 레버리지 배수

        Returns:
            (유효 여부, 사유)
        """
        if leverage > self.config.max_leverage:
            return False, f"레버리지 초과 (최대: {self.config.max_leverage}x)"

        return True, "OK"

