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
import json
import logging
import math
import time
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

_log = logging.getLogger("llmtrader.kimp_backtest_data")

__all__ = [
    "BacktestData",
    "load_backtest_bars",
    "selectable_interval_minutes",
    "UniverseBacktestItem",
    "run_universe_backtest",
]

_BACKTEST_CACHE_TTL_SEC = 900
_BACKTEST_MEM_CACHE: dict[str, tuple[float, "BacktestData"]] = {}

_BINANCE_MIN_INTERVAL_SEC = 0.08
_BINANCE_MAX_RETRIES = 7
_binance_throttle_lock = asyncio.Lock()
_binance_last_call_ts = 0.0


@dataclass(frozen=True)
class _Granularity:
    unit_min: int
    binance_interval: str


# 사용자가 직접 선택할 수 있는 캔들 간격(분 → 바이낸스 interval 코드).
# 업비트 분봉(1/3/5/15/30/60/240)과 바이낸스 선물 모두 지원하는 값만 노출한다.
_SELECTABLE_INTERVALS: dict[int, str] = {
    1: "1m",
    3: "3m",
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h",
    240: "4h",
}

# 공개 API 페이지네이션/429 폭주를 막기 위한 바 개수 상한.
# 현실적 조합(1분봉×7일≈1만, 5분봉×30일≈8.6천, 1시간봉×365일≈8.8천)은
# 허용하되, 1분봉×30일(≈4.3만)처럼 과도한 조합만 거부한다.
_MAX_BARS = 12000


def selectable_interval_minutes() -> list[int]:
    """UI에 노출할 선택 가능한 캔들 간격(분) 목록."""
    return sorted(_SELECTABLE_INTERVALS)


def _granularity_from_minutes(unit_min: int) -> _Granularity:
    interval_name = _SELECTABLE_INTERVALS.get(unit_min)
    if interval_name is None:
        allowed = ", ".join(f"{m}m" for m in sorted(_SELECTABLE_INTERVALS))
        raise ValueError(f"지원하지 않는 캔들 간격: {unit_min}분. 사용 가능: {allowed}")
    return _Granularity(unit_min, interval_name)


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


def _resolve_granularity(days: int, interval_min: int | None) -> _Granularity:
    """간격 미지정(None)이면 기간 기반 자동 선택, 지정되면 검증 후 사용.

    선택된 간격이 ``days`` 기간에서 ``_MAX_BARS`` 를 초과하는 바를 만들면
    공개 API 부하가 과도하므로 명확한 안내와 함께 거부한다.
    """
    if interval_min is None:
        return _pick_granularity(days)
    gran = _granularity_from_minutes(interval_min)
    est_bars = math.ceil(days * 24 * 60 / gran.unit_min)
    if est_bars > _MAX_BARS:
        raise ValueError(
            f"선택한 {interval_min}분봉 × {days}일은 약 {est_bars:,}개 바로 너무 많습니다"
            f"(상한 {_MAX_BARS:,}). 간격을 늘리거나 기간을 줄이세요."
        )
    return gran


def _backtest_cache_key(
    *,
    symbol: str,
    days: int,
    mode: KimpRateMode,
    include_funding: bool,
    unit_min: int,
    end_ms: int,
) -> str:
    return (
        f"kimp:backtest:v2:{mode}:{symbol}:{days}:"
        f"{int(include_funding)}:{unit_min}:{end_ms}"
    )


def _to_cache_payload(data: "BacktestData") -> dict[str, Any]:
    return {
        "n_funding_events": data.n_funding_events,
        "interval_min": data.interval_min,
        "bars": [
            [
                b.ts_ms,
                b.upbit_krw,
                b.binance_usdt,
                b.usd_krw,
                b.funding_rate,
            ]
            for b in data.bars
        ],
    }


def _from_cache_payload(payload: dict[str, Any]) -> "BacktestData" | None:
    try:
        bars = [
            KimpBar(
                ts_ms=int(row[0]),
                upbit_krw=float(row[1]),
                binance_usdt=float(row[2]),
                usd_krw=float(row[3]),
                funding_rate=(float(row[4]) if row[4] is not None else None),
            )
            for row in payload.get("bars", [])
            if isinstance(row, (list, tuple)) and len(row) == 5
        ]
        return BacktestData(
            bars=bars,
            n_funding_events=int(payload.get("n_funding_events") or 0),
            interval_min=int(payload["interval_min"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _get_cached_backtest_data(key: str) -> "BacktestData" | None:
    now = time.time()
    cached = _BACKTEST_MEM_CACHE.get(key)
    if cached and (now - cached[0]) < _BACKTEST_CACHE_TTL_SEC:
        return cached[1]

    try:
        from api.kline_cache import _get_redis

        redis = await _get_redis()
        if redis is None:
            return None
        packed = await redis.get(key)
        if packed is None:
            return None
        if isinstance(packed, bytes):
            packed = packed.decode("utf-8")
        payload = json.loads(str(packed))
        if not isinstance(payload, dict):
            return None
        data = _from_cache_payload(payload)
        if data is not None:
            _BACKTEST_MEM_CACHE[key] = (now, data)
        return data
    except Exception:
        _log.debug("KIMP backtest cache read failed", exc_info=True)
        return None


async def _set_cached_backtest_data(key: str, data: "BacktestData") -> None:
    _BACKTEST_MEM_CACHE[key] = (time.time(), data)
    try:
        from api.kline_cache import _get_redis

        redis = await _get_redis()
        if redis is None:
            return
        packed = json.dumps(_to_cache_payload(data), separators=(",", ":")).encode("utf-8")
        await redis.set(key, packed, ex=_BACKTEST_CACHE_TTL_SEC)
    except Exception:
        _log.debug("KIMP backtest cache write failed", exc_info=True)


async def _binance_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
) -> httpx.Response:
    """Throttle + 429/418 재시도가 적용된 Binance GET."""
    global _binance_last_call_ts
    last_exc: Exception | None = None
    for attempt in range(_BINANCE_MAX_RETRIES):
        async with _binance_throttle_lock:
            wait = _BINANCE_MIN_INTERVAL_SEC - (time.monotonic() - _binance_last_call_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            _binance_last_call_ts = time.monotonic()
        resp = await client.get(url, params=params)
        if resp.status_code not in (418, 429):
            resp.raise_for_status()
            return resp
        last_exc = httpx.HTTPStatusError(
            f"{resp.status_code} Too Many Requests",
            request=resp.request,
            response=resp,
        )
        retry_after = resp.headers.get("Retry-After")
        try:
            retry_after_sec = float(retry_after) if retry_after is not None else 0.0
        except ValueError:
            retry_after_sec = 0.0
        backoff = max(retry_after_sec, min(8.0, 0.6 * (2**attempt)))
        await asyncio.sleep(backoff)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("binance request failed")


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
        resp = await _binance_get(
            client,
            f"{BINANCE_FUTURES_BASE}/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": interval_name,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
        )
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
        try:
            resp = await _binance_get(
                client,
                f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate",
                {
                    "symbol": symbol,
                    "limit": 1000,
                    "startTime": cursor,
                    "endTime": end_ms,
                },
            )
        except httpx.HTTPStatusError:
            _log.warning("Binance funding history fetch failed symbol=%s", symbol, exc_info=True)
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
    interval_min: int | None = None,
) -> BacktestData:
    """``symbol`` 의 백테스트 바 시계열을 공개 API에서 구성해 반환한다.

    가격: 업비트 ``KRW-{sym}`` 캔들(롱) + 바이낸스 ``{sym}USDT`` 선물 캔들(숏).
    KRW 환산: ``rate_mode`` 에 따라 업비트 ``KRW-USDT`` 캔들 또는 은행 환율 상수.
    펀딩: ``include_funding`` 이면 정산 이력을 각 정산 바에 부착.
    캔들 간격: ``interval_min`` 미지정 시 기간 기반 자동, 지정 시 해당 분봉 사용.
    """
    sym = symbol.strip().upper()
    mode: KimpRateMode = "bank" if rate_mode == "bank" else "usdt"
    gran = _resolve_granularity(days, interval_min)
    interval = _interval_ms(gran.unit_min)
    now_ms = _floor_ms(int(datetime.now(UTC).timestamp() * 1000), interval)
    start_ms = now_ms - days * 24 * 3600 * 1000
    cache_key = _backtest_cache_key(
        symbol=sym,
        days=days,
        mode=mode,
        include_funding=include_funding,
        unit_min=gran.unit_min,
        end_ms=now_ms,
    )
    cached = await _get_cached_backtest_data(cache_key)
    if cached is not None:
        return cached

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

    data = BacktestData(
        bars=bars,
        n_funding_events=len(rate_by_ts),
        interval_min=gran.unit_min,
    )
    await _set_cached_backtest_data(cache_key, data)
    return data


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

    if len(data.bars) < min_bars:
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
