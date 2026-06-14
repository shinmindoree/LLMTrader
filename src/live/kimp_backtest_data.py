"""김프 중립 백테스트용 시세/펀딩 데이터 로더 (네트워크 계층).

순수 백테스트 엔진(:mod:`live.kimp_neutral_backtest`)이 소비할 ``KimpBar``
시계열을 외부 공개 API에서 구성한다. 가격 소스는 **업비트 KRW 현물 캔들 +
바이낸스 USDT-M 무기한 선물 캔들**(현물이 아닌 선물 종가)을 사용해, 실제
전략(현물 롱 + 선물 숏)의 손익 베이시스와 일치시킨다. 펀딩비 정산 이력을 함께
적재해 각 정산 바에 펀딩비를 부착한다.

KRW 환산 기준(``rate_mode``):

- ``usdt``: 업비트 ``KRW-USDT`` 캔들 종가(시점별 USDT/KRW). 라이브 김프 계산과
  동일한 기준이라 백테스트-라이브 일관성이 가장 높다.
- ``bank``: 현재 은행 USD/KRW 환율을 윈도우 전체에 상수로 적용.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from live.kimp_calculator import BINANCE_FUTURES_BASE
from live.kimp_candle_history import (
    KimpRateMode,
    _fetch_upbit_candles,
    _floor_ms,
    _interval_ms,
    _parse_float,
)
from live.kimp_neutral_backtest import (
    BacktestConfig,
    BacktestMetrics,
    KimpBar,
    composite_score,
    run_kimp_backtest,
)

__all__ = [
    "BacktestData",
    "load_backtest_bars",
    "UniverseBacktestItem",
    "run_universe_backtest",
]


@dataclass(frozen=True)
class _Granularity:
    unit_min: int
    binance_interval: str


def _pick_granularity(days: int) -> _Granularity:
    """조회 기간(일)에 맞춰 공개 API 페이지 수를 적정 범위로 묶는 캔들 간격 선택.

    각 구간의 바 개수가 대략 1,500~3,000 사이가 되도록 잡아 페이지네이션
    비용과 z-score 해상도를 절충한다.
    """
    if days <= 2:
        return _Granularity(1, "1m")
    if days <= 10:
        return _Granularity(5, "5m")
    if days <= 40:
        return _Granularity(15, "15m")
    if days <= 120:
        return _Granularity(60, "1h")
    return _Granularity(240, "4h")


async def _fetch_binance_futures_klines(  # noqa: PLR0913 — distinct pagination bounds
    client: httpx.AsyncClient,
    symbol: str,
    interval_name: str,
    unit_min: int,
    start_ms: int,
    end_ms: int,
) -> dict[int, float]:
    """바이낸스 USDT-M 선물 ``fapi/v1/klines`` 종가를 버킷별로 수집."""
    interval = _interval_ms(unit_min)
    cursor = start_ms
    out: dict[int, float] = {}
    max_pages = max(1, math.ceil(((end_ms - start_ms) / interval) / 1000) + 2)

    for _ in range(max_pages):
        resp = await client.get(
            f"{BINANCE_FUTURES_BASE}/fapi/v1/klines",
            params={
                "symbol": symbol,
                "interval": interval_name,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            break
        last_bucket = cursor
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            try:
                bucket = _floor_ms(int(row[0]), interval)
            except (TypeError, ValueError):
                continue
            price = _parse_float(row[4])
            if price is None:
                continue
            if start_ms <= bucket <= end_ms:
                out[bucket] = price
                last_bucket = max(last_bucket, bucket)
        next_cursor = last_bucket + interval
        if next_cursor <= cursor or next_cursor > end_ms:
            break
        cursor = next_cursor

    return out


async def _fetch_funding_history(
    client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int
) -> list[tuple[int, float]]:
    """바이낸스 ``fapi/v1/fundingRate`` 정산 이력을 ``[(ts_ms, rate)]`` 로 수집.

    ``rate`` 는 소수(예: 0.0001 = 0.01%). 실패 시 빈 리스트.
    """
    out: list[tuple[int, float]] = []
    cursor = max(0, start_ms)
    seen: set[int] = set()
    for _ in range(30):
        resp = await client.get(
            f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate",
            params={
                "symbol": symbol,
                "limit": 1000,
                "startTime": cursor,
                "endTime": end_ms,
            },
        )
        if resp.status_code != 200:
            break
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            break
        last_ts = cursor
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                ts = int(row["fundingTime"])
                rate = float(row["fundingRate"])
            except (KeyError, TypeError, ValueError):
                continue
            if ts in seen:
                continue
            seen.add(ts)
            out.append((ts, rate))
            last_ts = max(last_ts, ts)
        if len(rows) < 1000:
            break
        cursor = last_ts + 1
        if cursor >= end_ms:
            break

    out.sort(key=lambda x: x[0])
    return out


def _attach_funding(
    sorted_ts: list[int], funding_rows: list[tuple[int, float]]
) -> dict[int, float]:
    """각 펀딩 정산을 정산시각 **이상**인 첫 바 타임스탬프에 매핑.

    같은 바에 둘 이상 정산이 떨어지면 합산한다(코어스 캔들 대비 빈번한 정산 대비).
    """
    rate_by_ts: dict[int, float] = {}
    if not sorted_ts or not funding_rows:
        return rate_by_ts
    i = 0
    n = len(sorted_ts)
    for fts, rate in funding_rows:
        while i < n and sorted_ts[i] < fts:
            i += 1
        if i >= n:
            break
        target = sorted_ts[i]
        rate_by_ts[target] = rate_by_ts.get(target, 0.0) + rate
    return rate_by_ts


@dataclass(frozen=True)
class BacktestData:
    bars: list[KimpBar]
    n_funding_events: int
    interval_min: int


async def load_backtest_bars(
    symbol: str,
    *,
    days: int,
    rate_mode: KimpRateMode = "usdt",
    include_funding: bool = True,
) -> BacktestData:
    """``symbol`` 의 백테스트 바 시계열을 공개 API에서 구성해 반환한다.

    가격: 업비트 ``KRW-{sym}`` 캔들(롱) + 바이낸스 ``{sym}USDT`` 선물 캔들(숏).
    KRW 환산: ``rate_mode`` 에 따라 업비트 ``KRW-USDT`` 캔들 또는 은행 환율 상수.
    펀딩: ``include_funding`` 이면 정산 이력을 각 정산 바에 부착.
    """
    sym = symbol.strip().upper()
    mode: KimpRateMode = "bank" if rate_mode == "bank" else "usdt"
    gran = _pick_granularity(days)
    interval = _interval_ms(gran.unit_min)
    now_ms = _floor_ms(int(datetime.now(UTC).timestamp() * 1000), interval)
    start_ms = now_ms - days * 24 * 3600 * 1000

    async with httpx.AsyncClient(timeout=20.0) as client:
        upbit_coin_task = _fetch_upbit_candles(
            client, f"KRW-{sym}", gran.unit_min, start_ms, now_ms
        )
        binance_task = _fetch_binance_futures_klines(
            client, f"{sym}USDT", gran.binance_interval, gran.unit_min, start_ms, now_ms
        )
        funding_task: Any = (
            _fetch_funding_history(client, f"{sym}USDT", start_ms, now_ms)
            if include_funding
            else _noop_funding()
        )

        if mode == "bank":
            from live.fx_feed import get_fx_rate  # noqa: PLC0415

            upbit_coin, binance, funding_rows, bank_fx = await asyncio.gather(
                upbit_coin_task, binance_task, funding_task, get_fx_rate()
            )
            krw_rate_by_ts = {ts: bank_fx.rate for ts in binance}
        else:
            upbit_usdt_task = _fetch_upbit_candles(
                client, "KRW-USDT", gran.unit_min, start_ms, now_ms
            )
            upbit_coin, upbit_usdt, binance, funding_rows = await asyncio.gather(
                upbit_coin_task, upbit_usdt_task, binance_task, funding_task
            )
            krw_rate_by_ts = upbit_usdt

    common = sorted(set(upbit_coin) & set(krw_rate_by_ts) & set(binance))
    rate_by_ts = _attach_funding(common, funding_rows) if include_funding else {}

    bars: list[KimpBar] = []
    for ts in common:
        denom = binance[ts] * krw_rate_by_ts[ts]
        if denom <= 0:
            continue
        bars.append(
            KimpBar(
                ts_ms=ts,
                upbit_krw=upbit_coin[ts],
                binance_usdt=binance[ts],
                usd_krw=krw_rate_by_ts[ts],
                funding_rate=rate_by_ts.get(ts),
            )
        )

    return BacktestData(
        bars=bars,
        n_funding_events=len(rate_by_ts),
        interval_min=gran.unit_min,
    )


async def _noop_funding() -> list[tuple[int, float]]:
    return []


@dataclass(frozen=True)
class UniverseBacktestItem:
    """유니버스 백테스트 1종목 요약(랭킹용)."""

    symbol: str
    score: float
    metrics: BacktestMetrics | None
    n_bars: int
    n_funding_events: int
    error: str | None = None


async def _backtest_one(  # noqa: PLR0913 — keyword-only config knobs
    symbol: str,
    *,
    days: int,
    rate_mode: KimpRateMode,
    include_funding: bool,
    config: BacktestConfig,
    min_bars: int,
    sem: asyncio.Semaphore,
) -> UniverseBacktestItem:
    """한 종목 데이터 적재 + 백테스트 + 점수화(예외는 item.error 로 캡처)."""
    async with sem:
        try:
            data = await load_backtest_bars(
                symbol, days=days, rate_mode=rate_mode, include_funding=include_funding
            )
        except Exception as exc:  # noqa: BLE001
            return UniverseBacktestItem(
                symbol=symbol, score=float("-inf"), metrics=None,
                n_bars=0, n_funding_events=0, error=f"데이터 조회 실패: {exc}",
            )

    if len(data.bars) < max(min_bars, config.z_window):
        return UniverseBacktestItem(
            symbol=symbol, score=float("-inf"), metrics=None,
            n_bars=len(data.bars), n_funding_events=data.n_funding_events,
            error=f"데이터 부족({len(data.bars)} bars)",
        )

    result = run_kimp_backtest(data.bars, config)
    return UniverseBacktestItem(
        symbol=symbol,
        score=composite_score(result.metrics),
        metrics=result.metrics,
        n_bars=result.metrics.n_bars,
        n_funding_events=data.n_funding_events,
    )


async def run_universe_backtest(  # noqa: PLR0913 — keyword-only config knobs
    symbols: list[str],
    *,
    days: int = 30,
    rate_mode: KimpRateMode = "usdt",
    include_funding: bool = True,
    config: BacktestConfig | None = None,
    min_bars: int = 50,
    concurrency: int = 4,
) -> list[UniverseBacktestItem]:
    """``symbols`` 전체를 백테스트하고 ``composite_score`` 내림차순으로 정렬해 반환.

    공개 API 부하를 막기 위해 ``concurrency`` 개의 동시 적재로 제한한다. 실패한
    종목은 ``error`` 가 채워진 채 점수 ``-inf`` 로 하위에 남는다.
    """
    cfg = config or BacktestConfig()
    sem = asyncio.Semaphore(max(1, concurrency))
    tasks = [
        _backtest_one(
            sym.strip().upper(),
            days=days,
            rate_mode=rate_mode,
            include_funding=include_funding,
            config=cfg,
            min_bars=min_bars,
            sem=sem,
        )
        for sym in symbols
        if sym.strip()
    ]
    items = await asyncio.gather(*tasks)
    return sorted(items, key=lambda it: it.score, reverse=True)

