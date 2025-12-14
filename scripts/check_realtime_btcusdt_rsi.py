"""테스트넷 실시간 BTCUSDT 현재가 + RSI(14) 확인 스크립트.

출력:
- 현재가(Last Price): /fapi/v1/ticker/price
 - 최근 닫힌 1분봉 close 목록 기반 RSI(14)
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import typer

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings


def compute_rsi_from_closes(closes: list[float], period: int = 14) -> float:
    """마지막 (period+1)개의 종가로 RSI 계산(단순 평균 방식)."""
    if len(closes) < period + 1:
        return 50.0

    window = closes[-(period + 1) :]
    gains: list[float] = []
    losses: list[float] = []

    for i in range(1, len(window)):
        change = window[i] - window[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_realtime_rsi(closed_closes: list[float], last_price: float, period: int = 14) -> float:
    """실시간 RSI(=진행 중인 봉의 가격을 마지막 종가처럼 반영).

    - closed_closes: 닫힌 봉들의 종가 리스트
    - last_price: 현재 실시간 가격(Last Price)
    """
    if not closed_closes:
        return 50.0
    return compute_rsi_from_closes(closed_closes + [last_price], period=period)


app = typer.Typer(add_completion=False)


@app.command()
def main(
    symbol: str = typer.Option("BTCUSDT", "--symbol", "-s", help="심볼"),
    period: int = typer.Option(14, "--period", "-p", help="RSI 기간"),
    interval: str = typer.Option("1m", "--interval", "-i", help="캔들 간격(기본 1m)"),
    limit: int = typer.Option(200, "--limit", "-l", help="캔들 조회 개수(기본 200)"),
    watch: bool = typer.Option(False, "--watch", help="1초마다 실시간 로그 출력(무한 루프)"),
    every: float = typer.Option(1.0, "--every", help="watch 모드 로그 주기(초)"),
    count: int = typer.Option(0, "--count", help="watch 모드에서 출력 횟수(0이면 무한)"),
    realtime_rsi: bool = typer.Option(True, "--realtime-rsi/--no-realtime-rsi", help="실시간 RSI(진행중 봉 반영)도 함께 출력"),
) -> None:
    """테스트넷에서 현재가 + RSI를 출력합니다."""
    asyncio.run(
        _run(
            symbol=symbol,
            period=period,
            interval=interval,
            limit=limit,
            watch=watch,
            every=every,
            count=count,
            realtime_rsi=realtime_rsi,
        )
    )


async def _run(
    symbol: str,
    period: int,
    interval: str,
    limit: int,
    watch: bool,
    every: float,
    count: int,
    realtime_rsi: bool,
) -> None:
    s = get_settings()
    client = BinanceHTTPClient(
        api_key=s.binance.api_key,
        api_secret=s.binance.api_secret,
        base_url=s.binance.base_url,
    )

    try:
        # RSI는 1분봉 기준이라 매초 klines를 호출하면 과도합니다.
        # -> watch 모드에서는 1초마다 last_price만 갱신, RSI는 "닫힌 봉"이 바뀔 때만 갱신합니다.
        cached: dict[str, Any] = {
            "bar_open_ts": 0,
            "bar_close_ts": 0,
            "bar_close_price": 0.0,
            "rsi": 50.0,
            "closed_closes": [],
        }

        async def refresh_rsi_if_needed() -> None:
            # closeTime(k[6])을 기준으로 "닫힌 봉(last closed candle)"만 사용해 bar 시간이 흔들리지 않게 한다.
            now_ms = int(datetime.now().timestamp() * 1000)
            safe_ts = now_ms - 1500  # 네트워크/서버 지연 여유

            klines = await client.fetch_klines(symbol=symbol, interval=interval, limit=limit + 2)
            if not klines:
                return

            parsed: list[tuple[int, int, float]] = []
            for k in klines:
                try:
                    open_ts = int(k[0])
                    close_ts = int(k[6])
                    close_price = float(k[4])
                except Exception:  # noqa: BLE001
                    continue
                parsed.append((open_ts, close_ts, close_price))

            parsed.sort(key=lambda x: x[0])
            closed = [p for p in parsed if p[1] <= safe_ts]
            if not closed:
                return

            last_open_ts, last_close_ts, last_close_price = closed[-1]
            if last_open_ts == cached["bar_open_ts"]:
                return
            # API 응답이 간헐적으로 과거 봉으로 되돌아가는 경우가 있어(캐시/노드 흔들림),
            # bar 시간이 "왔다갔다" 하지 않도록 과거 값이면 무시한다.
            if cached["bar_open_ts"] and last_open_ts < cached["bar_open_ts"]:
                return

            closes = [p[2] for p in closed]
            rsi = compute_rsi_from_closes(closes, period=period)

            cached.update(
                {
                    "bar_open_ts": last_open_ts,
                    "bar_close_ts": last_close_ts,
                    "bar_close_price": last_close_price,
                    "rsi": rsi,
                    "closed_closes": closes,
                }
            )

        # 최초 1회 RSI 갱신
        await refresh_rsi_if_needed()

        if not watch:
            last_price = await client.fetch_ticker_price(symbol)
            rsi_rt = compute_realtime_rsi(cached["closed_closes"], last_price, period=period) if realtime_rsi else None
            now_local = datetime.now().isoformat(timespec="seconds")
            open_dt = (
                datetime.fromtimestamp(cached["bar_open_ts"] / 1000).isoformat(timespec="seconds")
                if cached["bar_open_ts"]
                else ""
            )
            close_dt = (
                datetime.fromtimestamp(cached["bar_close_ts"] / 1000).isoformat(timespec="seconds")
                if cached["bar_close_ts"]
                else ""
            )

            typer.echo("=" * 80)
            typer.echo(f"now(local)        : {now_local}")
            typer.echo(f"base_url          : {s.binance.base_url}")
            typer.echo(f"symbol            : {symbol}")
            typer.echo(f"last_price        : {last_price:,.2f}")
            if open_dt:
                typer.echo(f"last_closed_open  : {open_dt}")
            if close_dt:
                typer.echo(f"last_closed_close : {close_dt}")
            typer.echo(f"last_closed_price : {cached['bar_close_price']:,.2f}")
            typer.echo(f"rsi({period})      : {cached['rsi']:.2f}")
            if realtime_rsi and rsi_rt is not None:
                typer.echo(f"rsi_rt({period})   : {rsi_rt:.2f}  (using last price as forming candle)")
            typer.echo("=" * 80)
            return

        # watch 모드
        typer.echo("=" * 80)
        typer.echo(f"watch mode: base_url={s.binance.base_url} symbol={symbol} every={every}s period={period}")
        typer.echo("Press Ctrl+C to stop")
        typer.echo("=" * 80)

        n = 0
        while True:
            # 현재가(Last Price)는 매회 갱신
            last_price = await client.fetch_ticker_price(symbol)

            # RSI는 1분봉이 갱신되었을 가능성이 있을 때만 확인
            # (그래도 확실성을 위해 매회 확인하되, cached open_ts가 같으면 계산/출력만)
            await refresh_rsi_if_needed()

            now_local = datetime.now().isoformat(timespec="seconds")
            bar_dt = (
                datetime.fromtimestamp(cached["bar_open_ts"] / 1000).isoformat(timespec="minutes")
                if cached["bar_open_ts"]
                else ""
            )
            rsi_rt = compute_realtime_rsi(cached["closed_closes"], last_price, period=period) if realtime_rsi else None
            msg = (
                f"[{now_local}] "
                f"(bar={bar_dt}) "
                f"last={last_price:,.2f} "
                f"rsi({period})={cached['rsi']:.2f} "
            )
            if realtime_rsi and rsi_rt is not None:
                msg += f"rsi_rt({period})={rsi_rt:.2f} "
            msg += f"bar_close={cached['bar_close_price']:,.2f}"
            typer.echo(msg)

            n += 1
            if count and n >= count:
                break
            await asyncio.sleep(max(0.1, every))
    finally:
        await client.aclose()


if __name__ == "__main__":
    app()


