"""USD/KRW 환율 피드.

MVP는 Naver 단일 소스로 실시간 환율을 조회한다. 응답을 60초 TTL 메모리 캐시에
보관하여 외부 의존성 비용과 차단 위험을 줄인다.

- 1차 소스: Naver Finance 모바일 stock API
  ``GET https://m.stock.naver.com/front-api/marketIndex/productDetail
        ?category=exchange&reutersCode=FX_USDKRW``
  기본 응답 형식(2026-06 기준):
  ::
    {"isSuccess": true, "detailData": {"tradePrice": "1378.50", ...}}
- 2차 소스(폴백): Naver 검색 API 환율 위젯
  ``GET https://m.search.naver.com/p/csearch/content/qapirender.naver
        ?key=calculator&pkid=141&q=환율&where=m
        &u1=keb&u6=standardUnit&u7=0&u3=USD&u4=KRW&u8=down&u2=1``

호출자는 ``get_fx_rate()`` 만 사용하면 된다. 캐시 hit/miss 와 stale 여부가
``FxRate.stale`` 로 표현된다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

_log = logging.getLogger("llmtrader.fx_feed")

_NAVER_PRIMARY_URL = (
    "https://m.stock.naver.com/front-api/marketIndex/productDetail"
    "?category=exchange&reutersCode=FX_USDKRW"
)
_NAVER_FALLBACK_URL = (
    "https://m.search.naver.com/p/csearch/content/qapirender.naver"
    "?key=calculator&pkid=141&q=%ED%99%98%EC%9C%A8&where=m"
    "&u1=keb&u6=standardUnit&u7=0&u3=USD&u4=KRW&u8=down&u2=1"
)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

CACHE_TTL_SEC = 60.0
STALE_MAX_AGE_SEC = 6 * 60 * 60  # 6시간이 지난 캐시는 stale 플래그를 붙여 노출


@dataclass(frozen=True)
class FxRate:
    """USD/KRW 환율 + 메타데이터."""

    pair: str               # 항상 "USD/KRW"
    rate: float             # KRW per 1 USD
    source: str             # "naver" (현 MVP)
    fetched_at: datetime    # tz-aware UTC datetime
    stale: bool             # True 이면 캐시 fallback 이거나 fetch 실패로 직전 값 재사용


_cache_lock = asyncio.Lock()
_cached_rate: FxRate | None = None
_last_success_ts: float = 0.0


def _extract_from_naver_result(data: Any) -> float | None:
    """Naver Stock front-api 응답에서 환율 추출.

    실제 응답(2026-06 기준)::
        {"isSuccess": true,
         "result": {"closePrice": "1,526.90", ...}}

    과거 명세에 있던 ``detailData.tradePrice`` 도 함께 시도하여
    Naver 가 응답 형태를 약간 변경해도 견디도록 한다.
    """
    if not isinstance(data, dict):
        return None
    candidates: list[Any] = []
    result = data.get("result")
    if isinstance(result, dict):
        candidates += [result.get("closePrice"), result.get("tradePrice")]
        detail = result.get("detailData")
        if isinstance(detail, dict):
            candidates += [detail.get("tradePrice"), detail.get("closePrice")]
    detail_top = data.get("detailData")
    if isinstance(detail_top, dict):
        candidates += [detail_top.get("tradePrice"), detail_top.get("closePrice")]

    for raw in candidates:
        if raw is None:
            continue
        try:
            value = float(str(raw).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if 500.0 <= value <= 5000.0:
            return value
    return None


async def _fetch_naver_primary(client: httpx.AsyncClient) -> float | None:
    try:
        resp = await client.get(_NAVER_PRIMARY_URL, headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        data: Any = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        _log.warning("Naver primary fetch failed: %s", exc)
        return None
    return _extract_from_naver_result(data)


_FX_NUMBER_PATTERN = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]{3,5}\.[0-9]+)")


async def _fetch_naver_fallback(client: httpx.AsyncClient) -> float | None:
    try:
        resp = await client.get(_NAVER_FALLBACK_URL, headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        text = resp.text
    except httpx.HTTPError as exc:
        _log.warning("Naver fallback fetch failed: %s", exc)
        return None
    for match in _FX_NUMBER_PATTERN.finditer(text):
        try:
            candidate = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if 500.0 <= candidate <= 5000.0:
            return candidate
    return None


async def _fetch_fresh_rate() -> float | None:
    async with httpx.AsyncClient(timeout=8.0) as client:
        value = await _fetch_naver_primary(client)
        if value is not None and 500.0 <= value <= 5000.0:
            return value
        return await _fetch_naver_fallback(client)


async def get_fx_rate(force_refresh: bool = False) -> FxRate:
    """USD/KRW 현재 환율을 반환한다.

    - ``force_refresh=False``일 때 60초 TTL 캐시에서 우선 응답한다.
    - 외부 호출 실패 시 직전 성공 값을 ``stale=True`` 로 재사용한다.
    - 직전 값도 없으면 ``RuntimeError`` 를 던진다.
    """
    global _cached_rate, _last_success_ts
    loop = asyncio.get_running_loop()
    now_ts = loop.time()

    if (
        not force_refresh
        and _cached_rate is not None
        and (now_ts - _last_success_ts) < CACHE_TTL_SEC
    ):
        return _cached_rate

    async with _cache_lock:
        now_ts = loop.time()
        if (
            not force_refresh
            and _cached_rate is not None
            and (now_ts - _last_success_ts) < CACHE_TTL_SEC
        ):
            return _cached_rate

        fresh = await _fetch_fresh_rate()
        if fresh is not None:
            _cached_rate = FxRate(
                pair="USD/KRW",
                rate=fresh,
                source="naver",
                fetched_at=datetime.now(timezone.utc),
                stale=False,
            )
            _last_success_ts = now_ts
            return _cached_rate

        if _cached_rate is not None:
            age = now_ts - _last_success_ts
            stale_age = age > STALE_MAX_AGE_SEC
            _cached_rate = FxRate(
                pair=_cached_rate.pair,
                rate=_cached_rate.rate,
                source=_cached_rate.source,
                fetched_at=datetime.now(timezone.utc),
                stale=True if stale_age else True,
            )
            return _cached_rate

        raise RuntimeError("FX rate fetch failed and no cache available")


def reset_cache_for_tests() -> None:
    """테스트 격리용 캐시 초기화 헬퍼."""
    global _cached_rate, _last_success_ts
    _cached_rate = None
    _last_success_ts = 0.0
