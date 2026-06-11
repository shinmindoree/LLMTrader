"""External-candle Kimchi Premium history with result caching.

Builds chart history from Upbit KRW coin candles, Upbit KRW-USDT candles, and
Binance spot klines so historical and live KIMP share the same USDT/KRW basis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import StatisticsError, mean, pstdev
from typing import Any, Literal

import httpx

from live.kimp_calculator import BINANCE_PUBLIC_BASE, UPBIT_PUBLIC_BASE

_log = logging.getLogger("llmtrader.kimp_candle_history")

KimpHistoryRange = Literal["1H", "1D", "7D", "30D", "ALL"]


@dataclass(frozen=True)
class _RangeConfig:
    duration: timedelta
    upbit_unit_min: int
    binance_interval: str
    ttl_sec: int
    max_points: int = 2000


_RANGE_CONFIGS: dict[KimpHistoryRange, _RangeConfig] = {
    "1H": _RangeConfig(timedelta(hours=1), 1, "1m", ttl_sec=20, max_points=500),
    "1D": _RangeConfig(timedelta(days=1), 1, "1m", ttl_sec=60, max_points=2000),
    "7D": _RangeConfig(timedelta(days=7), 5, "5m", ttl_sec=300, max_points=2000),
    "30D": _RangeConfig(timedelta(days=30), 15, "15m", ttl_sec=900, max_points=2000),
    # "ALL" is bounded to one year for public API cost control; the response is cached longer.
    "ALL": _RangeConfig(timedelta(days=365), 240, "4h", ttl_sec=3600, max_points=2000),
}


@dataclass(frozen=True)
class KimpCandleHistory:
    symbol: str
    range: KimpHistoryRange
    as_of: datetime
    mean_pct: float | None
    std_pct: float | None
    n_samples: int
    series: list[tuple[int, float]]


_MEM_CACHE: dict[str, tuple[float, KimpCandleHistory]] = {}


def _cache_key(symbol: str, range_name: KimpHistoryRange) -> str:
    return f"kimp:history:v2:{symbol}:{range_name}"


def _interval_ms(unit_min: int) -> int:
    return unit_min * 60_000


def _floor_ms(ts_ms: int, interval_ms: int) -> int:
    return ts_ms - (ts_ms % interval_ms)


def _parse_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _parse_upbit_ts_ms(row: dict[str, Any]) -> int | None:
    raw = row.get("candle_date_time_utc")
    if isinstance(raw, str) and raw:
        try:
            return int(datetime.fromisoformat(raw).replace(tzinfo=UTC).timestamp() * 1000)
        except ValueError:
            pass
    try:
        return int(row.get("timestamp"))
    except (TypeError, ValueError):
        return None


def _to_cache_payload(history: KimpCandleHistory) -> dict[str, Any]:
    return {
        "symbol": history.symbol,
        "range": history.range,
        "as_of": history.as_of.isoformat(),
        "mean_pct": history.mean_pct,
        "std_pct": history.std_pct,
        "n_samples": history.n_samples,
        "series": history.series,
    }


def _from_cache_payload(payload: dict[str, Any]) -> KimpCandleHistory | None:
    try:
        raw_range = str(payload["range"])
        if raw_range not in _RANGE_CONFIGS:
            return None
        as_of = datetime.fromisoformat(str(payload["as_of"]))
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)
        series: list[tuple[int, float]] = []
        for raw in payload.get("series", []):
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                continue
            ts = int(raw[0])
            premium = float(raw[1])
            if math.isfinite(premium):
                series.append((ts, premium))
        return KimpCandleHistory(
            symbol=str(payload["symbol"]).upper(),
            range=raw_range,  # type: ignore[arg-type]
            as_of=as_of,
            mean_pct=(
                float(payload["mean_pct"])
                if payload.get("mean_pct") is not None
                else None
            ),
            std_pct=(
                float(payload["std_pct"])
                if payload.get("std_pct") is not None
                else None
            ),
            n_samples=int(payload.get("n_samples") or 0),
            series=series,
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _get_cached_history(key: str, ttl_sec: int) -> KimpCandleHistory | None:
    now = time.time()
    cached = _MEM_CACHE.get(key)
    if cached and (now - cached[0]) < ttl_sec:
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
        history = _from_cache_payload(payload)
        if history is not None:
            _MEM_CACHE[key] = (now, history)
        return history
    except Exception:
        _log.debug("KIMP history cache read failed", exc_info=True)
        return None


async def _set_cached_history(key: str, history: KimpCandleHistory, ttl_sec: int) -> None:
    _MEM_CACHE[key] = (time.time(), history)
    try:
        from api.kline_cache import _get_redis

        redis = await _get_redis()
        if redis is None:
            return
        packed = json.dumps(_to_cache_payload(history), separators=(",", ":")).encode("utf-8")
        await redis.set(key, packed, ex=ttl_sec)
    except Exception:
        _log.debug("KIMP history cache write failed", exc_info=True)


async def _fetch_upbit_candles(
    client: httpx.AsyncClient,
    market: str,
    unit_min: int,
    start_ms: int,
    end_ms: int,
) -> dict[int, float]:
    url = f"{UPBIT_PUBLIC_BASE}/v1/candles/minutes/{unit_min}"
    interval = _interval_ms(unit_min)
    cursor = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    out: dict[int, float] = {}
    max_pages = max(1, math.ceil(((end_ms - start_ms) / interval) / 200) + 2)

    for _ in range(max_pages):
        resp = await client.get(
            url,
            params={
                "market": market,
                "to": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "count": 200,
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            break

        oldest: int | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts_ms = _parse_upbit_ts_ms(row)
            price = _parse_float(row.get("trade_price"))
            if ts_ms is None or price is None:
                continue
            bucket = _floor_ms(ts_ms, interval)
            oldest = bucket if oldest is None else min(oldest, bucket)
            if start_ms <= bucket <= end_ms:
                out[bucket] = price

        if oldest is None or oldest <= start_ms:
            break
        cursor = datetime.fromtimestamp((oldest - 1) / 1000, tz=UTC)

    return out


async def _fetch_binance_klines(
    client: httpx.AsyncClient,
    symbol: str,
    interval_name: str,
    unit_min: int,
    start_ms: int,
    end_ms: int,
) -> dict[int, float]:
    interval = _interval_ms(unit_min)
    cursor = start_ms
    out: dict[int, float] = {}
    max_pages = max(1, math.ceil(((end_ms - start_ms) / interval) / 1000) + 2)

    for _ in range(max_pages):
        resp = await client.get(
            f"{BINANCE_PUBLIC_BASE}/api/v3/klines",
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


def _downsample(points: list[tuple[int, float]], max_points: int) -> list[tuple[int, float]]:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    indexes = {int(i * step) for i in range(max_points)}
    indexes.add(len(points) - 1)
    sampled = [points[i] for i in sorted(indexes)]
    if len(sampled) > max_points:
        sampled = [*sampled[: max_points - 1], points[-1]]
    return sampled


def _stats(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    try:
        return mean(values), (pstdev(values) if len(values) >= 2 else 0.0)
    except StatisticsError:
        return mean(values), 0.0


async def get_kimp_candle_history(
    symbol: str,
    range_name: KimpHistoryRange,
) -> KimpCandleHistory:
    sym = symbol.strip().upper()
    config = _RANGE_CONFIGS[range_name]
    key = _cache_key(sym, range_name)
    cached = await _get_cached_history(key, config.ttl_sec)
    if cached is not None:
        return cached

    interval = _interval_ms(config.upbit_unit_min)
    now_ms = _floor_ms(int(datetime.now(UTC).timestamp() * 1000), interval)
    start_ms = now_ms - int(config.duration.total_seconds() * 1000)

    async with httpx.AsyncClient(timeout=20.0) as client:
        upbit_coin_task = _fetch_upbit_candles(
            client, f"KRW-{sym}", config.upbit_unit_min, start_ms, now_ms
        )
        upbit_usdt_task = _fetch_upbit_candles(
            client, "KRW-USDT", config.upbit_unit_min, start_ms, now_ms
        )
        binance_task = _fetch_binance_klines(
            client,
            f"{sym}USDT",
            config.binance_interval,
            config.upbit_unit_min,
            start_ms,
            now_ms,
        )
        upbit_coin, upbit_usdt, binance = await asyncio.gather(
            upbit_coin_task, upbit_usdt_task, binance_task
        )

    points: list[tuple[int, float]] = []
    for ts in sorted(set(upbit_coin) & set(upbit_usdt) & set(binance)):
        denom = binance[ts] * upbit_usdt[ts]
        if denom <= 0:
            continue
        premium = (upbit_coin[ts] / denom) - 1.0
        if math.isfinite(premium):
            points.append((ts, premium))

    values = [p for _, p in points]
    mean_pct, std_pct = _stats(values)
    history = KimpCandleHistory(
        symbol=sym,
        range=range_name,
        as_of=datetime.fromtimestamp(now_ms / 1000, tz=UTC),
        mean_pct=mean_pct,
        std_pct=std_pct,
        n_samples=len(points),
        series=_downsample(points, config.max_points),
    )
    await _set_cached_history(key, history, config.ttl_sec)
    return history


__all__ = ["KimpCandleHistory", "get_kimp_candle_history"]
