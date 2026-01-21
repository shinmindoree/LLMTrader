"""전략 실행 컨텍스트 정의."""

from collections.abc import Callable
from typing import Any, Protocol


class StrategyContext(Protocol):
    """전략이 사용하는 공통 컨텍스트 인터페이스."""

    @property
    def current_price(self) -> float:
        """현재 가격."""
        ...

    @property
    def position_size(self) -> float:
        """현재 포지션 크기 (양수: 롱, 음수: 숏, 0: 없음)."""
        ...

    @property
    def position_entry_price(self) -> float:
        """현재 포지션의 진입가 (포지션 없으면 0)."""
        ...

    @property
    def unrealized_pnl(self) -> float:
        """미실현 손익."""
        ...

    @property
    def balance(self) -> float:
        """계좌 잔고."""
        ...

    def buy(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        """매수 주문 (price=None이면 시장가)."""
        ...

    def sell(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        """매도 주문 (price=None이면 시장가)."""
        ...

    def close_position(
        self,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        """현재 포지션 전체 청산."""
        ...

    def calc_entry_quantity(self, entry_pct: float | None = None, price: float | None = None) -> float:
        """시스템 설정 기반으로 진입 수량 계산."""
        ...

    def enter_long(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        """시스템 리스크 설정 기반으로 롱 진입."""
        ...

    def enter_short(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        """시스템 리스크 설정 기반으로 숏 진입."""
        ...

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """지표 조회 (예: SMA, RSI 등)."""
        ...

    def register_indicator(self, name: str, func: Callable[..., Any]) -> None:
        """커스텀 지표 등록."""
        ...

    def get_open_orders(self) -> list[dict[str, Any]]:
        """현재 미체결 주문 목록.

        - 라이브 트레이딩에서는 실제 거래소 미체결 주문을 반환한다.
        - 백테스트에서는 항상 빈 리스트를 반환한다.
        """
        ...
