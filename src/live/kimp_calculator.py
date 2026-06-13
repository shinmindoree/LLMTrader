"""김치 프리미엄(Kimchi Premium) 계산 + Upbit/Binance 공개 시세 조회.

역김프 델타중립 전략(업비트 현물 롱 + 바이낸스 무기한 숏)에 맞춰 Binance
**USDT-M 무기한 선물 마크가격**을 기준으로 김프를 계산한다.

수식:
    kimp = (upbit_krw_price / (binance_mark_price * usdt_krw_rate)) - 1

공개 endpoint 만 사용하므로 인증이 필요 없다.
- Upbit 시세: ``GET https://api.upbit.com/v1/ticker?markets=KRW-USDT,KRW-BTC``
- Binance 선물 시세/펀딩: ``GET https://fapi.binance.com/fapi/v1/premiumIndex``
  (markPrice · lastFundingRate · nextFundingTime 을 1콜로 전체 심볼 반환)
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
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# Binance 무기한 펀딩 기본 정산 주기(시간). premiumIndex 는 주기를 주지 않으므로
# 대부분의 USDT-M 계약 기본값인 8시간을 사용한다.
DEFAULT_FUNDING_INTERVAL_HOURS = 8.0

# 유니버스 조회 실패 시 폴백용 기본 심볼셋 (Upbit·Binance 선물 공통 대표 종목).
DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "LINK", "AVAX")

# ── 역김프 델타중립 시그널 임계값 ──────────────────────────
# 진입: 역김프(저평가)일수록, 30일 평균 대비 충분히 낮을수록 매력적.
SIGNAL_ENTRY_KIMP = -0.003   # 김프 <= -0.3% (역김프)
SIGNAL_ENTRY_Z = -1.0        # z-score <= -1.0 (평균 대비 저평가)
# 청산: 정김프로 회귀하거나 평균 수준 이상으로 비싸지면 스프레드 차익 실현.
SIGNAL_EXIT_KIMP = 0.0       # 김프 >= 0% (정김프 회귀)
SIGNAL_EXIT_Z = 0.5          # z-score >= 0.5 (평균 이상)


def classify_kimp_signal(kimp_pct: float, zscore: float | None) -> str:
    """역김프 전략용 진입/청산/관망 시그널을 분류한다.

    - ``"entry"`` : 역김프 + 평균 대비 저평가 → 현물 매수 + 선물 숏 진입 후보
    - ``"exit"``  : 정김프 회귀 또는 평균 이상 → 청산 후보
    - ``"hold"``  : 그 외 관망

    z-score 표본이 없으면(``None``) 김프 수준만으로 보수적으로 판단한다.
    """
    if zscore is None:
        if kimp_pct <= SIGNAL_ENTRY_KIMP:
            return "entry"
        if kimp_pct >= SIGNAL_EXIT_KIMP:
            return "exit"
        return "hold"
    if kimp_pct <= SIGNAL_ENTRY_KIMP and zscore <= SIGNAL_ENTRY_Z:
        return "entry"
    if kimp_pct >= SIGNAL_EXIT_KIMP or zscore >= SIGNAL_EXIT_Z:
        return "exit"
    return "hold"


@dataclass(frozen=True)
class KimpRow:
    symbol: str                 # 예: "BTC"
    upbit_krw_price: float
    binance_usdt_price: float   # Binance USDT-M 무기한 마크가격 (USDT/coin)
    usdt_krw_rate: float
    kimp_pct: float             # 예: 0.0345 == 3.45% (선물 마크 기준)
    rate_source: str
    rate_stale: bool
    funding_rate: float = 0.0           # 직전 펀딩 비율 (예: 0.0001 == 0.01%)
    next_funding_time: datetime | None = None
    funding_interval_hours: float = DEFAULT_FUNDING_INTERVAL_HOURS
    upbit_quote_volume_krw: float = 0.0  # Upbit 24h 누적 거래대금(KRW)
    binance_spot_price: float | None = None   # Binance 현물 가격 (USDT/coin)
    spot_kimp_pct: float | None = None         # 현물 기준 김프 (USDT 환율 기준)

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


async def _fetch_upbit_tickers(
    client: httpx.AsyncClient, symbols: list[str]
) -> dict[str, dict[str, float]]:
    """Upbit KRW 마켓 티커: base 자산별 현재가 + 24h 누적 거래대금(KRW).

    Upbit ``/v1/ticker`` 는 ``markets`` 쿼리 길이 제한이 있어 큰 유니버스는
    배치로 나눠 조회한다.
    """
    out: dict[str, dict[str, float]] = {}
    batch = 90  # 마켓 코드 길이를 고려한 보수적 배치 크기
    for i in range(0, len(symbols), batch):
        chunk = symbols[i : i + batch]
        markets = ",".join(f"KRW-{s}" for s in chunk)
        try:
            resp = await client.get(
                f"{UPBIT_PUBLIC_BASE}/v1/ticker", params={"markets": markets}
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            _log.warning("Upbit ticker fetch failed: %s", exc)
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            market = str(item.get("market") or "")
            if not market.startswith("KRW-"):
                continue
            symbol = market.split("-", 1)[1]
            try:
                price = float(item.get("trade_price") or 0.0)
            except (TypeError, ValueError):
                continue
            try:
                vol24h = float(item.get("acc_trade_price_24h") or 0.0)
            except (TypeError, ValueError):
                vol24h = 0.0
            out[symbol] = {"price": price, "vol24h": vol24h}
    return out


async def _fetch_upbit_prices(client: httpx.AsyncClient, symbols: list[str]) -> dict[str, float]:
    """base 자산별 현재가만 반환(하위 호환용 wrapper)."""
    tickers = await _fetch_upbit_tickers(client, symbols)
    return {sym: row["price"] for sym, row in tickers.items()}


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


async def _fetch_binance_futures(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    """Binance USDT-M 무기한 선물 ``premiumIndex`` 전체 조회.

    한 번의 호출로 모든 심볼의 마크가격·직전 펀딩비·다음 펀딩시각을 반환한다.
    base 자산(``BTC``)을 키로, ``{"mark","funding","next_funding_ms"}`` 를 값으로.
    """
    try:
        resp = await client.get(f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex")
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        _log.warning("Binance premiumIndex fetch failed: %s", exc)
        return {}
    out: dict[str, dict[str, float]] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            pair = str(item.get("symbol") or "")
            if not pair.endswith("USDT"):
                continue
            base = pair[:-4]
            try:
                mark = float(item.get("markPrice") or 0.0)
            except (TypeError, ValueError):
                continue
            if mark <= 0:
                continue
            try:
                funding = float(item.get("lastFundingRate") or 0.0)
            except (TypeError, ValueError):
                funding = 0.0
            try:
                next_ms = float(item.get("nextFundingTime") or 0.0)
            except (TypeError, ValueError):
                next_ms = 0.0
            out[base] = {"mark": mark, "funding": funding, "next_funding_ms": next_ms}
    return out


async def _fetch_binance_spot(client: httpx.AsyncClient) -> dict[str, float]:
    """Binance 현물 ``/api/v3/ticker/price`` 전체 조회.

    한 번의 호출로 모든 현물 심볼의 최신 체결가를 반환한다. USDT 마켓만 추려서
    base 자산(``BTC``)을 키로, 현물 가격(USDT/coin)을 값으로 돌려준다.
    """
    try:
        resp = await client.get(f"{BINANCE_PUBLIC_BASE}/api/v3/ticker/price")
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        _log.warning("Binance spot ticker fetch failed: %s", exc)
        return {}
    out: dict[str, float] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            pair = str(item.get("symbol") or "")
            if not pair.endswith("USDT"):
                continue
            base = pair[:-4]
            try:
                price = float(item.get("price") or 0.0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            out[base] = price
    return out


async def compute_kimp_snapshot(symbols: list[str] | None = None) -> KimpSnapshot:
    """주어진 심볼들의 김프 스냅샷을 계산한다.

    Binance USDT-M 무기한 선물 마크가격을 기준으로 김프를 산출하며, 직전 펀딩비와
    다음 펀딩시각을 함께 수집한다. ``symbols`` 미지정 시 Upbit·Binance 선물 공통
    상장 유니버스를 사용한다. 실패한 심볼은 ``errors`` 에 기록되고 ``rows`` 에서는
    제외된다.
    """
    if symbols is None:
        # 지연 import: kimp_universe 가 이 모듈을 import 하므로 순환을 피한다.
        from live.kimp_universe import get_kimp_universe  # noqa: PLC0415

        symbols = await get_kimp_universe()
    target_symbols = [s for s in list(symbols) if s != "USDT"]
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        upbit_task = _fetch_upbit_tickers(client, ["USDT", *target_symbols])
        binance_task = _fetch_binance_futures(client)
        binance_spot_task = _fetch_binance_spot(client)
        upbit_tickers, binance_futures, binance_spot = await asyncio.gather(
            upbit_task, binance_task, binance_spot_task
        )

    usdt_ticker = upbit_tickers.get("USDT")
    usdt_krw = usdt_ticker["price"] if usdt_ticker else None
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
        upbit_row = upbit_tickers.get(sym)
        fut = binance_futures.get(sym)
        krw = upbit_row["price"] if upbit_row else None
        mark = fut["mark"] if fut else None
        if not krw or not mark:
            errors.append(f"{sym}: missing price data")
            continue
        denom = mark * usdt_krw
        if denom <= 0:
            errors.append(f"{sym}: invalid denominator")
            continue
        kimp = (krw / denom) - 1.0
        spot_price = binance_spot.get(sym)
        spot_kimp = None
        if spot_price and spot_price > 0:
            spot_denom = spot_price * usdt_krw
            if spot_denom > 0:
                spot_kimp = (krw / spot_denom) - 1.0
        next_ms = fut.get("next_funding_ms", 0.0) if fut else 0.0
        next_funding = (
            datetime.fromtimestamp(next_ms / 1000.0, tz=timezone.utc)
            if next_ms and next_ms > 0
            else None
        )
        rows.append(
            KimpRow(
                symbol=sym,
                upbit_krw_price=krw,
                binance_usdt_price=mark,
                usdt_krw_rate=usdt_krw,
                kimp_pct=kimp,
                rate_source="upbit",
                rate_stale=False,
                funding_rate=fut.get("funding", 0.0) if fut else 0.0,
                next_funding_time=next_funding,
                funding_interval_hours=DEFAULT_FUNDING_INTERVAL_HOURS,
                upbit_quote_volume_krw=(upbit_row.get("vol24h", 0.0) if upbit_row else 0.0),
                binance_spot_price=spot_price,
                spot_kimp_pct=spot_kimp,
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
