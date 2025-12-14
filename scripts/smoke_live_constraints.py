"""라이브 주문 스모크 테스트(체결 여부만 빠르게 확인).

동작:
1) 레버리지 설정
2) 선물 계좌 조회 (walletBalance 기반)
3) max_notional = equity * leverage * max_position 계산
4) target_notional(기본: max_notional의 일부)만큼 MARKET BUY
5) 즉시 reduceOnly MARKET SELL로 청산

주의:
- 테스트넷/소액으로만 사용하세요.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_DOWN
from typing import Any

import typer

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings


app = typer.Typer(add_completion=False)


def _round_down(x: float, step: float) -> float:
    if step <= 0:
        return x
    # float // float는 0.013000000000000001 같은 노이즈를 만들 수 있어 Decimal로 처리
    dx = Decimal(str(x))
    ds = Decimal(str(step))
    q = (dx / ds).to_integral_value(rounding=ROUND_DOWN) * ds
    return float(q)


@app.command()
def main(
    symbol: str = typer.Option("BTCUSDT", help="심볼"),
    leverage: int = typer.Option(5, help="레버리지(예: 5)"),
    max_position: float = typer.Option(1.0, help="최대 포지션(자산 대비, 예: 1.0)"),
    fraction: float = typer.Option(
        0.05,
        help="max_notional 대비 실제 테스트 진입 비율(기본 5%). 1.0이면 최대치로 진입(위험).",
    ),
    qty_step: float = typer.Option(0.001, help="수량 스텝(대략값, 기본 0.001)"),
    min_qty: float = typer.Option(0.001, help="최소 수량(대략값, 기본 0.001)"),
    recv_window: int = typer.Option(10_000, help="recvWindow(ms)"),
) -> None:
    asyncio.run(_run(symbol, leverage, max_position, fraction, qty_step, min_qty, recv_window))


async def _run(
    symbol: str,
    leverage: int,
    max_position: float,
    fraction: float,
    qty_step: float,
    min_qty: float,
    recv_window: int,
) -> None:
    if leverage <= 0:
        raise typer.BadParameter("leverage must be > 0")
    if not (0 < max_position <= 1.0):
        raise typer.BadParameter("max_position must be in (0, 1]")
    if not (0 < fraction <= 1.0):
        raise typer.BadParameter("fraction must be in (0, 1]")
    if qty_step <= 0 or min_qty <= 0:
        raise typer.BadParameter("qty_step/min_qty must be > 0")

    s = get_settings()
    client = BinanceHTTPClient(
        api_key=s.binance.api_key,
        api_secret=s.binance.api_secret,
        base_url=s.binance.base_url,
    )

    try:
        # 1) 레버리지 설정
        await client._signed_request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": int(leverage), "recvWindow": recv_window},
        )

        # 2) 계좌 조회 (walletBalance)
        account: dict[str, Any] = await client._signed_request(
            "GET", "/fapi/v2/account", {"recvWindow": recv_window}
        )
        wallet = account.get("walletBalance")
        if wallet is None:
            wallet = account.get("totalWalletBalance")
        if wallet is None:
            wallet = account.get("availableBalance", 0)
        equity = float(wallet)

        # 3) 가격 + max_notional 계산
        last = await client.fetch_ticker_price(symbol)
        max_notional = equity * float(leverage) * float(max_position)
        target_notional = max_notional * float(fraction)
        raw_qty = target_notional / last if last > 0 else 0.0
        qty = _round_down(raw_qty, qty_step)
        if qty < min_qty:
            qty = min_qty

        typer.echo("=" * 80)
        typer.echo(f"symbol={symbol}")
        typer.echo(f"leverage={leverage}x, max_position={max_position}, fraction={fraction}")
        typer.echo(f"equity(walletBalance)={equity:,.2f} USDT")
        typer.echo(f"last={last:,.2f}")
        typer.echo(f"max_notional={max_notional:,.2f}  target_notional={target_notional:,.2f}")
        typer.echo(f"qty(raw)={raw_qty:.6f}  qty(step)={qty:.6f}")
        typer.echo("=" * 80)

        # 4) BUY
        buy = await client.place_order(
            symbol,
            "BUY",
            qty,
            type="MARKET",
            recvWindow=recv_window,
        )
        typer.echo(f"BUY response: {buy}")

        # 5) 즉시 청산(감축)
        await asyncio.sleep(0.2)
        sell = await client.place_order(
            symbol,
            "SELL",
            qty,
            type="MARKET",
            reduceOnly=True,
            recvWindow=recv_window,
        )
        typer.echo(f"SELL(reduceOnly) response: {sell}")
        typer.echo("✅ smoke test done")
    finally:
        await client.aclose()


if __name__ == "__main__":
    app()


