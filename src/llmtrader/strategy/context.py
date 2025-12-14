"""전략 실행 컨텍스트 정의."""

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

    def buy(self, quantity: float, price: float | None = None) -> None:
        """매수 주문 (price=None이면 시장가)."""
        ...

    def sell(self, quantity: float, price: float | None = None) -> None:
        """매도 주문 (price=None이면 시장가)."""
        ...

    def close_position(self) -> None:
        """현재 포지션 전체 청산."""
        ...

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """지표 조회 (예: SMA, RSI 등)."""
        ...




