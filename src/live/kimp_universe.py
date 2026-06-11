"""김프 스크리너 유니버스 — Upbit KRW 현물 ∩ Binance USDT-M 무기한 선물.

역김프 델타중립 전략(업비트 현물 롱 + 바이낸스 무기한 숏)은 **두 거래소에서
동시에 거래 가능한 코인**만 대상으로 한다. 이 모듈은 두 거래소의 상장 목록을
주기적으로 조회해 교집합(base 자산 기준)을 캐시한다.

- Upbit 현물:  ``GET https://api.upbit.com/v1/market/all`` 의 ``KRW-*`` 마켓
- Binance 선물: ``GET https://fapi.binance.com/fapi/v1/exchangeInfo`` 의
  ``status=TRADING`` & ``contractType=PERPETUAL`` & ``quoteAsset=USDT`` 심볼

공개 endpoint 만 사용하므로 인증이 필요 없다. 조회 실패 시 마지막으로 성공한
유니버스를 반환하여 스크리너 가용성을 해치지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from live.kimp_calculator import (
    DEFAULT_SYMBOLS,
    UPBIT_PUBLIC_BASE,
)

_log = logging.getLogger("llmtrader.kimp_universe")

BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# 유니버스는 상장 변화가 느리므로 10분 캐시면 충분하다.
_UNIVERSE_TTL_SEC = 600.0

# base 자산명이 절대 김프 대상이 될 수 없는 항목(스테이블/환율 기준 등).
_EXCLUDED_BASES: frozenset[str] = frozenset({"USDT", "USDC", "DAI", "TUSD", "FDUSD", "BUSD"})

_cache: dict[str, object] = {"symbols": (), "ts": 0.0}
_lock = asyncio.Lock()


async def _fetch_upbit_krw_bases(client: httpx.AsyncClient) -> set[str]:
    """Upbit KRW 마켓에 상장된 base 자산 집합 (예: {"BTC", "ETH", ...})."""
    resp = await client.get(f"{UPBIT_PUBLIC_BASE}/v1/market/all", params={"isDetails": "false"})
    resp.raise_for_status()
    data = resp.json()
    bases: set[str] = set()
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            market = str(item.get("market") or "")
            if not market.startswith("KRW-"):
                continue
            base = market.split("-", 1)[1].strip().upper()
            if base:
                bases.add(base)
    return bases


async def _fetch_binance_perp_bases(client: httpx.AsyncClient) -> set[str]:
    """Binance USDT-M 무기한 선물(TRADING) base 자산 집합."""
    resp = await client.get(f"{BINANCE_FUTURES_BASE}/fapi/v1/exchangeInfo")
    resp.raise_for_status()
    data = resp.json()
    bases: set[str] = set()
    symbols = data.get("symbols", []) if isinstance(data, dict) else []
    for raw in symbols:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("status") or "").upper() != "TRADING":
            continue
        if str(raw.get("contractType") or "").upper() != "PERPETUAL":
            continue
        if str(raw.get("quoteAsset") or "").upper() != "USDT":
            continue
        base = str(raw.get("baseAsset") or "").strip().upper()
        if base:
            bases.add(base)
    return bases


async def _compute_universe() -> list[str]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        upbit_task = _fetch_upbit_krw_bases(client)
        binance_task = _fetch_binance_perp_bases(client)
        upbit_bases, binance_bases = await asyncio.gather(upbit_task, binance_task)

    common = (upbit_bases & binance_bases) - _EXCLUDED_BASES
    return sorted(common)


def _cached_fresh(force: bool) -> list[str] | None:
    """캐시가 TTL 내면 복사본을 반환, 아니면 ``None``."""
    if force:
        return None
    cached = tuple(_cache.get("symbols") or ())  # type: ignore[arg-type]
    cached_ts = float(_cache.get("ts") or 0.0)  # type: ignore[arg-type]
    if cached and (time.monotonic() - cached_ts) < _UNIVERSE_TTL_SEC:
        return list(cached)
    return None


def _fallback() -> list[str]:
    """마지막 성공 유니버스, 없으면 기본 심볼셋."""
    cached = tuple(_cache.get("symbols") or ())  # type: ignore[arg-type]
    return list(cached) if cached else list(DEFAULT_SYMBOLS)


async def get_kimp_universe(force: bool = False) -> list[str]:
    """두 거래소 공통 상장 코인(base) 목록을 반환한다 (TTL 캐시).

    조회 실패 시 마지막 성공 결과를 반환하고, 그것도 없으면 ``DEFAULT_SYMBOLS``
    로 폴백한다.
    """
    fresh = _cached_fresh(force)
    if fresh is not None:
        return fresh

    async with _lock:
        # 락 진입 후 재확인(다른 코루틴이 막 갱신했을 수 있음).
        fresh = _cached_fresh(force)
        if fresh is not None:
            return fresh

        try:
            symbols = await _compute_universe()
        except Exception as exc:  # noqa: BLE001
            _log.warning("kimp universe fetch failed: %s", exc)
            symbols = []

        if not symbols:
            return _fallback()

        _cache["symbols"] = tuple(symbols)
        _cache["ts"] = time.monotonic()
        return symbols


__all__ = ["get_kimp_universe"]
