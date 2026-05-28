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

    def flip_position(
        self,
        target_side: int,
        close_reason: str | None = None,
        entry_reason: str | None = None,
        entry_pct: float | None = None,
        use_chase: bool | None = None,
    ) -> None:
        """현재 포지션을 반대 방향으로 플립(청산 + 반대 방향 신규 진입).

        Args:
            target_side: 새로 진입할 방향. +1=롱, -1=숏.
            close_reason: 기존 포지션 청산 사유.
            entry_reason: 새 포지션 진입 사유.
            entry_pct: 진입 비율(생략 시 시스템 기본 설정 사용).
            use_chase: Chase Order 사용 여부(None=라이브 기본 설정 따름).

        동작:
            - 현재 포지션이 0이면 ``target_side`` 방향으로 단순 진입한다.
            - 현재 포지션 방향이 ``target_side``와 같으면 아무것도 하지 않는다.
            - 반대 방향이면 청산 + 신규 진입을 수행한다.
                * 백테스트: 같은 봉에서 동기로 두 주문이 순차 체결된다.
                * 라이브: 청산 주문을 먼저 보내고, 청산 체결이 확인된
                  직후(inflight 락 해제 직후) 신규 진입을 자동으로
                  발주한다. 이를 통해 ``close_position`` 직후
                  ``enter_short``를 호출했을 때 ``position.size != 0``
                  가드와 ``_order_inflight`` 가드 때문에 신규 진입이
                  드롭되는 라이브-백테스트 괴리를 제거한다.
        """
        ...

    def add_to_long(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        """기존 롱 포지션에 피라미딩 추가 진입. max_pyramid_entries 한도 내에서만 허용."""
        ...

    def add_to_short(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        """기존 숏 포지션에 피라미딩 추가 진입. max_pyramid_entries 한도 내에서만 허용."""
        ...

    @property
    def pyramid_count(self) -> int:
        """현재 피라미딩 횟수 (최초 진입 제외)."""
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
