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

    async def drain_async(self) -> None:
        """라이브 모드에서 런너가 SIGTERM/SIGINT 등으로 정상 종료될 때,
        프로세스가 죽기 직전에 호출되는 비동기 훅 (선택).

        전략은 이 훅에서 In-memory 상태를 영속 저장소(Redis 등)에 마지막으로
        기록해야 한다. 새 레플리카는 다음 시작 시 이 스냅샷을 읽어 ``warmup``
        을 건너뛰고 즉시 같은 상태에서 재개한다. 호출은 best-effort 이며,
        예외는 호출자가 잡아 무시한다.
        """
