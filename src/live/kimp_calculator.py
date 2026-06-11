"""김치 프리미엄(Kimchi Premium) 계산 + Upbit/Binance 공개 가격 조회.

수식:
    kimp = (upbit_krw_price / (binance_usdt_price * usdt_krw_rate)) - 1

공개 endpoint 만 사용하므로 인증이 필요 없다.
- Upbit 시세: ``GET https://api.upbit.com/v1/ticker?markets=KRW-USDT,KRW-BTC``
- Binance 현물 시세: ``GET https://api.binance.com/api/v3/ticker/price?symbols=...``
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

_log = logging.getLogger("llmtrader.kimp_calculator")

UPBIT_PUBLIC_BASE = "https://api.upbit.com"
BINANCE_PUBLIC_BASE = "https://api.binance.com"

# MVP 기본 모니터링 심볼셋. 향후 사용자 설정으로 확장 가능.
DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "LINK", "AVAX")


@dataclass(frozen=True)
class KimpRow:
    symbol: str                 # 예: "BTC"
    upbit_krw_price: float
    binance_usdt_price: float
    usdt_krw_rate: float
    kimp_pct: float             # 예: 0.0345 == 3.45%
    rate_source: str
    rate_stale: bool

    @property
    def usd_krw_rate(self) -> float:
        """Backward-compatible alias for DB/API fields that still use the old name."""
        return self.usdt_krw_rate

    @property
    def fx_source(self) -> str:
        return self.rate_source

    @property
    def fx_stale(self) -> bool:
        return self.rate_stale


@dataclass(frozen=True)
class KimpRate:
    pair: str
    rate: float
    source: str
    fetched_at: datetime
    stale: bool = False


@dataclass
class KimpSnapshot:
    rows: list[KimpRow]
    rate: KimpRate
    as_of: datetime
    errors: list[str] = field(default_factory=list)

    @property
    def fx(self) -> KimpRate:
        """Backward-compatible alias for callers that still refer to the quote as fx."""
        return self.rate


async def _fetch_upbit_prices(client: httpx.AsyncClient, symbols: list[str]) -> dict[str, float]:
    markets = ",".join(f"KRW-{s}" for s in symbols)
    try:
        resp = await client.get(
            f"{UPBIT_PUBLIC_BASE}/v1/ticker", params={"markets": markets}
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        _log.warning("Upbit ticker fetch failed: %s", exc)
        return {}
    out: dict[str, float] = {}
    if isinstance(data, list):
        for item in data:
            market = item.get("market", "")
            if not market.startswith("KRW-"):
                continue
            symbol = market.split("-", 1)[1]
            try:
                out[symbol] = float(item.get("trade_price") or 0.0)
            except (TypeError, ValueError):
                continue
    return out


async def get_usdt_krw_rate() -> KimpRate:
    """Return the tradable USDT/KRW reference price from Upbit KRW-USDT."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        prices = await _fetch_upbit_prices(client, ["USDT"])
    rate = prices.get("USDT")
    if not rate or rate <= 0:
        raise RuntimeError("Upbit KRW-USDT price is unavailable")
    return KimpRate(
        pair="USDT/KRW",
        rate=rate,
        source="upbit",
        fetched_at=datetime.now(timezone.utc),
        stale=False,
    )


async def _fetch_binance_prices(client: httpx.AsyncClient, symbols: list[str]) -> dict[str, float]:
    payload = json.dumps([f"{s}USDT" for s in symbols], separators=(",", ":"))
    try:
        resp = await client.get(
            f"{BINANCE_PUBLIC_BASE}/api/v3/ticker/price", params={"symbols": payload}
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        _log.warning("Binance ticker fetch failed: %s", exc)
        return {}
    out: dict[str, float] = {}
    if isinstance(data, list):
        for item in data:
            pair = item.get("symbol", "")
            if not pair.endswith("USDT"):
                continue
            symbol = pair[:-4]
            try:
                out[symbol] = float(item.get("price") or 0.0)
            except (TypeError, ValueError):
                continue
    return out


async def compute_kimp_snapshot(symbols: list[str] | None = None) -> KimpSnapshot:
    """주어진 심볼들의 김프 스냅샷을 계산한다.

    실패한 심볼은 ``errors`` 에 기록되고 ``rows`` 에서는 제외된다.
    """
    target_symbols = [s for s in list(symbols or DEFAULT_SYMBOLS) if s != "USDT"]
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=8.0) as client:
        upbit_task = _fetch_upbit_prices(client, ["USDT", *target_symbols])
        binance_task = _fetch_binance_prices(client, target_symbols)
        upbit_prices, binance_prices = await asyncio.gather(upbit_task, binance_task)

    usdt_krw = upbit_prices.get("USDT")
    as_of = datetime.now(timezone.utc)
    if not usdt_krw or usdt_krw <= 0:
        return KimpSnapshot(
            rows=[],
            rate=KimpRate(
                pair="USDT/KRW",
                rate=0.0,
                source="upbit",
                fetched_at=as_of,
                stale=True,
            ),
            as_of=as_of,
            errors=["USDT/KRW: missing Upbit KRW-USDT price"],
        )

    rows: list[KimpRow] = []
    for sym in target_symbols:
        krw = upbit_prices.get(sym)
        usdt = binance_prices.get(sym)
        if not krw or not usdt:
            errors.append(f"{sym}: missing price data")
            continue
        denom = usdt * usdt_krw
        if denom <= 0:
            errors.append(f"{sym}: invalid denominator")
            continue
        kimp = (krw / denom) - 1.0
        rows.append(
            KimpRow(
                symbol=sym,
                upbit_krw_price=krw,
                binance_usdt_price=usdt,
                usdt_krw_rate=usdt_krw,
                kimp_pct=kimp,
                rate_source="upbit",
                rate_stale=False,
            )
        )

    return KimpSnapshot(
        rows=rows,
        rate=KimpRate(
            pair="USDT/KRW",
            rate=usdt_krw,
            source="upbit",
            fetched_at=as_of,
            stale=False,
        ),
        as_of=as_of,
        errors=errors,
    )
