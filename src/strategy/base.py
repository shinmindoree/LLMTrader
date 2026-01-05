"""전략 베이스 클래스 정의."""

from abc import ABC, abstractmethod
from typing import Any

from strategy.context import StrategyContext


class Strategy(ABC):
    """전략 베이스 클래스. 모든 전략은 이 클래스를 상속해야 함."""

    def __init__(self) -> None:
        """전략 초기화."""
        self.params: dict[str, Any] = {}

    @abstractmethod
    def initialize(self, ctx: StrategyContext) -> None:
        """전략 초기화 (백테스트/라이브 시작 시 1회 호출).

        Args:
            ctx: 전략 실행 컨텍스트
        """

    @abstractmethod
    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        """새 바(캔들) 도착 시 호출.

        Args:
            ctx: 전략 실행 컨텍스트
            bar: 캔들 데이터 {timestamp, open, high, low, close, volume}
        """

    def on_trade(self, ctx: StrategyContext, trade: dict[str, Any]) -> None:
        """체결 발생 시 호출 (선택).

        Args:
            ctx: 전략 실행 컨텍스트
            trade: 체결 정보
        """

    def on_order(self, ctx: StrategyContext, order: dict[str, Any]) -> None:
        """주문 상태 변경 시 호출 (선택).

        Args:
            ctx: 전략 실행 컨텍스트
            order: 주문 정보
        """

    def finalize(self, ctx: StrategyContext) -> None:
        """전략 종료 시 호출 (선택)."""

