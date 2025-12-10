import asyncio
from typing import Optional

import typer
from httpx import HTTPStatusError

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings

app = typer.Typer(add_completion=False, help="바이낸스 테스트넷 최소 주문/취소 스모크")


async def _place_and_cancel(
    symbol: str,
    qty: float,
    side: str,
    order_type: str,
    price: Optional[float],
    recv_window: int,
) -> None:
    settings = get_settings()
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )

    params = {"type": order_type.upper()}
    if params["type"] == "LIMIT":
        if price is None:
            raise ValueError("LIMIT 주문에는 --price 가 필요합니다.")
        params.update({"price": price, "timeInForce": "GTC"})

    try:
        order = await client.place_order(
            symbol, side.upper(), qty, recvWindow=recv_window, **params
        )
        print("order response:", order)
        order_id = order.get("orderId")
        status = (order.get("status") or "").upper()

        if not order_id:
            print("orderId 없음 → 취소 스킵")
            return

        if status in {"FILLED", "CANCELED", "EXPIRED"}:
            print(f"주문 상태가 {status} → 취소 스킵")
            return

        try:
            cancel = await client.cancel_order(symbol, order_id)
            print("cancel response:", cancel)
        except HTTPStatusError as cancel_err:
            print("cancel failed:", cancel_err.response.text)
    except HTTPStatusError as order_err:
        print("order failed:", order_err.response.text)
    finally:
        await client.aclose()


@app.command()
def main(
    symbol: str = typer.Option("BTCUSDT", help="거래 심볼"),
    qty: float = typer.Option(0.001, help="주문 수량"),
    side: str = typer.Option("BUY", help="BUY 또는 SELL"),
    order_type: str = typer.Option(
        "MARKET",
        help="주문 타입 MARKET 또는 LIMIT (LIMIT 시 --price 필수)",
    ),
    price: Optional[float] = typer.Option(None, help="LIMIT 주문 가격"),
    recv_window: int = typer.Option(10_000, help="recvWindow(ms)"),
) -> None:
    asyncio.run(_place_and_cancel(symbol, qty, side, order_type, price, recv_window))


if __name__ == "__main__":
    app()




