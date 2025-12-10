from typing import Protocol, runtime_checkable


@runtime_checkable
class BinanceMarketDataClient(Protocol):
    """캔들/시세 조회용 프로토콜."""

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 500,
    ) -> list[dict]:
        ...


@runtime_checkable
class BinanceTradingClient(Protocol):
    """주문/포지션 관리 프로토콜."""

    async def place_order(self, symbol: str, side: str, quantity: float, **params: object) -> dict:
        ...

    async def cancel_order(self, symbol: str, order_id: int) -> dict:
        ...

    async def fetch_position(self, symbol: str) -> dict:
        ...




