"""펀딩비 차익거래 엔진 — 현물 롱 + 선물 숏 (Delta-Neutral).

전략 흐름:
  1. 매 10초 펀딩비 조회 (GET /fapi/v1/premiumIndex)
  2. 연환산 펀딩비 > entry_deadband → 진입 (현물 매수 + 선물 숏)
  3. 연환산 펀딩비 < exit_deadband  → 언와인딩 (분할 지정가 청산)
  4. 선물 마진 비율 > margin_alert_ratio → 현물→선물 자동 이체

설계 노트:
  - 사용자별 엔진 레지스트리(``_engines``)로 멀티유저 격리.
  - 포지션/메트릭은 ``account_snapshots`` (key=``funding_arb:{user_id}``)에 영속화하여
    프로세스 재시작 후 중복 진입을 방지.
  - 테스트넷/메인넷 모두 현물·선물 base URL을 프로필에서 도출.
  - 주문 수량은 exchangeInfo(LOT_SIZE / minNotional)에 맞춰 step 단위로 내림.
  - 펀딩 주기(fundingIntervalHours)를 조회해 연환산 계수를 동적으로 계산.
  - 펀딩 수익(income)·미실현 손익을 주기적으로 갱신.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.schemas import FundingArbitrageParams, FundingArbitrageStatusResponse
from control.repo import get_account_snapshot, upsert_account_snapshot

_log = logging.getLogger("llmtrader.funding_arb")

_DEFAULT_FUNDING_INTERVAL_HOURS = 8.0
_HOURS_PER_YEAR = 365 * 24
_POLL_INTERVAL_SEC = 10
_METRICS_REFRESH_EVERY_TICKS = 6  # ~60s
_UNWIND_SLICES = 4
_UNWIND_INTERVAL_SEC = 3
_MIN_TRANSFER_USDT = 1.0


def _snapshot_key(user_id: str) -> str:
    return f"funding_arb:{user_id}"


def _periods_per_year(interval_hours: float) -> float:
    return _HOURS_PER_YEAR / interval_hours if interval_hours > 0 else 1095.0


# ── 심볼 필터 캐시 ──────────────────────────────────────────


@dataclass
class _SymbolFilter:
    step_size: float
    min_qty: float
    min_notional: float


_filter_cache: dict[tuple[str, str], _SymbolFilter] = {}


@dataclass
class _EngineState:
    user_id: str
    running: bool = False
    symbol: str | None = None
    spot_qty: float = 0.0
    futures_short_qty: float = 0.0
    entry_mark_price: float = 0.0
    entry_ts_ms: int | None = None
    funding_interval_hours: float = _DEFAULT_FUNDING_INTERVAL_HOURS
    current_funding_rate: float | None = None
    unrealized_pnl: float | None = None
    accumulated_funding_income: float = 0.0
    last_funding_ts: datetime | None = None
    params: FundingArbitrageParams | None = None
    api_key: str = ""          # futures api key
    api_secret: str = ""       # futures api secret
    spot_api_key: str = ""     # spot api key (differs from futures on testnet)
    spot_api_secret: str = ""  # spot api secret (differs from futures on testnet)
    spot_base: str = "https://api.binance.com"
    futures_base: str = "https://fapi.binance.com"
    is_testnet: bool = False
    session_maker: async_sessionmaker[AsyncSession] | None = field(default=None, repr=False)
    _tick_count: int = 0
    _task: asyncio.Task[None] | None = field(default=None, repr=False, compare=False)


_engines: dict[str, _EngineState] = {}


# ── 퍼블릭 인터페이스 ──────────────────────────────────────


def get_engine_status(user_id: str) -> FundingArbitrageStatusResponse:
    st = _engines.get(user_id)
    if st is None:
        return FundingArbitrageStatusResponse(running=False, accumulated_funding_income=0.0)
    ann = (
        st.current_funding_rate * _periods_per_year(st.funding_interval_hours) * 100
        if st.current_funding_rate is not None
        else None
    )
    return FundingArbitrageStatusResponse(
        running=st.running,
        symbol=st.symbol,
        spot_qty=st.spot_qty or None,
        futures_short_qty=st.futures_short_qty or None,
        current_funding_rate=st.current_funding_rate,
        annualized_funding_pct=ann,
        unrealized_pnl=st.unrealized_pnl,
        accumulated_funding_income=st.accumulated_funding_income,
        last_funding_ts=st.last_funding_ts,
        params=st.params,
    )


async def start_engine(
    *,
    user_id: str,
    params: FundingArbitrageParams,
    futures_api_key: str,
    futures_api_secret: str,
    spot_api_key: str,
    spot_api_secret: str,
    is_testnet: bool,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    existing = _engines.get(user_id)
    if existing and existing.running:
        _log.warning("Engine already running for user=%s — ignoring start", user_id)
        return

    spot_base, futures_base = _bases_for_env(is_testnet)
    st = _EngineState(
        user_id=user_id,
        running=True,
        symbol=params.symbol,
        params=params,
        api_key=futures_api_key,
        api_secret=futures_api_secret,
        spot_api_key=spot_api_key,
        spot_api_secret=spot_api_secret,
        spot_base=spot_base,
        futures_base=futures_base,
        is_testnet=is_testnet,
        session_maker=session_maker,
    )
    _engines[user_id] = st

    if session_maker is not None:
        await _restore_state(st)

    st._task = asyncio.create_task(_engine_loop(st), name=f"funding_arb_{user_id}")
    _log.info(
        "Funding arbitrage engine started: user=%s symbol=%s testnet=%s",
        user_id,
        params.symbol,
        is_testnet,
    )


async def stop_engine(user_id: str) -> None:
    st = _engines.get(user_id)
    if not st or not st.running:
        return
    st.running = False
    if st._task and not st._task.done():
        st._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await st._task
    _log.info("Funding arbitrage engine stopped: user=%s", user_id)


# ── base URL 도출 ──────────────────────────────────────────


def _bases_for_env(is_testnet: bool) -> tuple[str, str]:
    """(spot_base, futures_base)를 반환."""
    if is_testnet:
        return "https://testnet.binance.vision", "https://testnet.binancefuture.com"
    return "https://api.binance.com", "https://fapi.binance.com"


# ── 영속화 ─────────────────────────────────────────────────


async def _persist_state(st: _EngineState) -> None:
    if st.session_maker is None:
        return

    data = {
        "running": st.running,
        "symbol": st.symbol,
        "spot_qty": st.spot_qty,
        "futures_short_qty": st.futures_short_qty,
        "entry_mark_price": st.entry_mark_price,
        "entry_ts_ms": st.entry_ts_ms,
        "funding_interval_hours": st.funding_interval_hours,
        "accumulated_funding_income": st.accumulated_funding_income,
        "params": st.params.model_dump() if st.params else None,
    }
    try:
        async with st.session_maker() as session:
            await upsert_account_snapshot(session, key=_snapshot_key(st.user_id), data_json=data)
            await session.commit()
    except Exception:
        _log.exception("Failed to persist funding-arb state for user=%s", st.user_id)


async def _restore_state(st: _EngineState) -> None:
    if st.session_maker is None:
        return

    try:
        async with st.session_maker() as session:
            snap = await get_account_snapshot(session, key=_snapshot_key(st.user_id))
    except Exception:
        _log.exception("Failed to restore funding-arb state for user=%s", st.user_id)
        return
    if not snap or not snap.data_json:
        return
    data = snap.data_json
    # 같은 심볼에 대한 미청산 포지션만 복원
    if data.get("symbol") == st.symbol:
        st.spot_qty = float(data.get("spot_qty") or 0.0)
        st.futures_short_qty = float(data.get("futures_short_qty") or 0.0)
        st.entry_mark_price = float(data.get("entry_mark_price") or 0.0)
        st.entry_ts_ms = data.get("entry_ts_ms")
        st.accumulated_funding_income = float(data.get("accumulated_funding_income") or 0.0)
        if st.spot_qty or st.futures_short_qty:
            _log.info(
                "Restored open position user=%s spot=%.6f short=%.6f",
                st.user_id,
                st.spot_qty,
                st.futures_short_qty,
            )


# ── 내부 루프 ──────────────────────────────────────────────


async def _engine_loop(st: _EngineState) -> None:
    async with (
        httpx.AsyncClient(base_url=st.futures_base, timeout=10.0) as futures_client,
        httpx.AsyncClient(base_url=st.spot_base, timeout=10.0) as spot_client,
    ):
        # 펀딩 주기 1회 조회 (연환산 계수)
        try:
            st.funding_interval_hours = await _fetch_funding_interval(
                futures_client, st.symbol or ""
            )
        except Exception:
            _log.warning(
                "fundingInfo fetch failed — defaulting to %sh", _DEFAULT_FUNDING_INTERVAL_HOURS
            )

        while st.running:
            try:
                await _tick(st, futures_client=futures_client, spot_client=spot_client)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception(
                    "Tick error (user=%s) — retry in %ds", st.user_id, _POLL_INTERVAL_SEC
                )
            await asyncio.sleep(_POLL_INTERVAL_SEC)


async def _tick(
    st: _EngineState,
    *,
    futures_client: httpx.AsyncClient,
    spot_client: httpx.AsyncClient,
) -> None:
    params = st.params
    assert params is not None
    st._tick_count += 1

    funding_rate, mark_price = await _fetch_funding_rate(futures_client, params.symbol)
    st.current_funding_rate = funding_rate
    st.last_funding_ts = datetime.now(UTC)
    ppy = _periods_per_year(st.funding_interval_hours)
    ann_pct = funding_rate * ppy * 100

    has_position = st.spot_qty > 0.0 or st.futures_short_qty > 0.0

    # 마진 방어 + 메트릭 갱신 (포지션 보유 시)
    if has_position:
        await _check_margin_and_rebalance(
            st, futures_client=futures_client, spot_client=spot_client, mark_price=mark_price
        )
        if st._tick_count % _METRICS_REFRESH_EVERY_TICKS == 0:
            await _refresh_metrics(st, futures_client=futures_client, mark_price=mark_price)

    no_position = not has_position

    entry_threshold_ann = params.entry_deadband_pct * ppy
    if no_position and ann_pct > entry_threshold_ann:
        _log.info(
            "ENTER signal user=%s: annualized=%.2f%% > threshold=%.2f%%",
            st.user_id,
            ann_pct,
            entry_threshold_ann,
        )
        await _enter_position(
            st, futures_client=futures_client, spot_client=spot_client, mark_price=mark_price
        )
        return

    exit_threshold_ann = params.exit_deadband_pct * ppy
    if has_position and ann_pct < exit_threshold_ann:
        _log.info(
            "EXIT signal user=%s: annualized=%.2f%% < exit_threshold=%.2f%%",
            st.user_id,
            ann_pct,
            exit_threshold_ann,
        )
        await _unwind_position(st, futures_client=futures_client, spot_client=spot_client)


# ── 시세/펀딩 조회 ─────────────────────────────────────────


async def _fetch_funding_rate(client: httpx.AsyncClient, symbol: str) -> tuple[float, float]:
    resp = await client.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    rate = float(data.get("lastFundingRate") or 0)
    mark_price = float(data.get("markPrice") or 0)
    return rate, mark_price


async def _fetch_funding_interval(client: httpx.AsyncClient, symbol: str) -> float:
    resp = await client.get("/fapi/v1/fundingInfo")
    resp.raise_for_status()
    rows: list[dict[str, Any]] = resp.json()
    for row in rows:
        if row.get("symbol") == symbol:
            hours = float(row.get("fundingIntervalHours") or _DEFAULT_FUNDING_INTERVAL_HOURS)
            return hours if hours > 0 else _DEFAULT_FUNDING_INTERVAL_HOURS
    return _DEFAULT_FUNDING_INTERVAL_HOURS


# ── 심볼 필터 (LOT_SIZE / minNotional) ─────────────────────


def _round_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    return math.floor(qty / step) * step


async def _get_filter(
    client: httpx.AsyncClient, base_url: str, symbol: str, *, is_futures: bool
) -> _SymbolFilter:
    cache_key = (base_url, symbol)
    cached = _filter_cache.get(cache_key)
    if cached is not None:
        return cached

    if is_futures:
        resp = await client.get("/fapi/v1/exchangeInfo")
        resp.raise_for_status()
        symbols = resp.json().get("symbols", [])
        info = next((s for s in symbols if s.get("symbol") == symbol), None)
    else:
        resp = await client.get("/api/v3/exchangeInfo", params={"symbol": symbol})
        resp.raise_for_status()
        symbols = resp.json().get("symbols", [])
        info = symbols[0] if symbols else None

    step_size = 0.0
    min_qty = 0.0
    min_notional = 0.0
    if info:
        for flt in info.get("filters", []):
            ftype = flt.get("filterType")
            if ftype == "LOT_SIZE":
                step_size = float(flt.get("stepSize") or 0)
                min_qty = float(flt.get("minQty") or 0)
            elif ftype in ("MIN_NOTIONAL", "NOTIONAL"):
                min_notional = float(flt.get("minNotional") or flt.get("notional") or 0)

    result = _SymbolFilter(step_size=step_size, min_qty=min_qty, min_notional=min_notional)
    _filter_cache[cache_key] = result
    return result


# ── 마진 감시/리밸런싱 ─────────────────────────────────────


async def _check_margin_and_rebalance(  # noqa: PLR0911
    st: _EngineState,
    *,
    futures_client: httpx.AsyncClient,
    spot_client: httpx.AsyncClient,
    mark_price: float,
) -> None:
    params = st.params
    assert params is not None
    if st.futures_short_qty == 0.0 or mark_price <= 0:
        return
    if st.is_testnet:
        return  # Universal Transfer(sapi)는 테스트넷 미지원

    headers = _auth_headers(st.api_key)
    try:
        resp = await futures_client.get(
            "/fapi/v2/account",
            headers=headers,
            params=_signed_params(st.api_secret, {}),
        )
        resp.raise_for_status()
        acc: dict[str, Any] = resp.json()
    except Exception:
        _log.warning("Failed to fetch futures account for margin check (user=%s)", st.user_id)
        return

    total_margin = float(acc.get("totalMarginBalance") or 0)
    maint_margin = float(acc.get("totalMaintMargin") or 0)
    if total_margin <= 0:
        return

    margin_ratio = maint_margin / total_margin
    if margin_ratio < params.margin_alert_ratio:
        return

    _log.warning(
        "Margin ratio %.2f >= alert %.2f (user=%s) — auto-rebalancing",
        margin_ratio,
        params.margin_alert_ratio,
        st.user_id,
    )
    try:
        spot_headers = _auth_headers(st.spot_api_key)
        spot_resp = await spot_client.get(
            "/api/v3/account",
            headers=spot_headers,
            params=_signed_params(st.spot_api_secret, {}),
        )
        spot_resp.raise_for_status()
        balances: list[dict[str, Any]] = spot_resp.json().get("balances", [])
        usdt_free = next(
            (float(b["free"]) for b in balances if b.get("asset") == "USDT"),
            0.0,
        )
    except Exception:
        _log.warning("Failed to fetch spot balance for rebalance (user=%s)", st.user_id)
        return

    transfer_amt = usdt_free * params.rebalance_transfer_pct
    if transfer_amt < _MIN_TRANSFER_USDT:
        return

    try:
        await _universal_transfer(
            spot_client=spot_client,
            api_key=st.spot_api_key,
            api_secret=st.spot_api_secret,
            transfer_type="MAIN_UMFUTURE",
            asset="USDT",
            amount=transfer_amt,
        )
        _log.info("Transferred %.2f USDT spot→futures (user=%s)", transfer_amt, st.user_id)
    except Exception:
        _log.exception("Auto-rebalance transfer failed (user=%s)", st.user_id)


# ── 진입/청산 ──────────────────────────────────────────────


async def _enter_position(
    st: _EngineState,
    *,
    futures_client: httpx.AsyncClient,
    spot_client: httpx.AsyncClient,
    mark_price: float,
) -> None:
    params = st.params
    assert params is not None
    if mark_price <= 0:
        return

    spot_flt = await _get_filter(spot_client, st.spot_base, params.symbol, is_futures=False)
    fut_flt = await _get_filter(futures_client, st.futures_base, params.symbol, is_futures=True)

    # 현물·선물 수량을 동일하게 유지하기 위해 더 거친 step/min을 사용
    step = max(spot_flt.step_size, fut_flt.step_size)
    min_qty = max(spot_flt.min_qty, fut_flt.min_qty)
    min_notional = max(spot_flt.min_notional, fut_flt.min_notional)

    raw_qty = params.allocated_usdt / mark_price
    qty = _round_step(raw_qty, step)
    if qty < min_qty or qty <= 0:
        _log.warning(
            "Entry skipped (user=%s): qty %.8f below min_qty %.8f", st.user_id, qty, min_qty
        )
        return
    if min_notional and qty * mark_price < min_notional:
        _log.warning(
            "Entry skipped (user=%s): notional %.2f below min %.2f",
            st.user_id,
            qty * mark_price,
            min_notional,
        )
        return

    qty_str = _fmt_qty(qty, step)

    # 1) 현물 시장가 매수
    try:
        spot_order = await _place_spot_order(
            client=spot_client,
            api_key=st.spot_api_key,
            api_secret=st.spot_api_secret,
            symbol=params.symbol,
            side="BUY",
            qty_str=qty_str,
        )
        filled = float(spot_order.get("executedQty") or qty)
        st.spot_qty = filled
        _log.info("Spot BUY filled (user=%s): qty=%s", st.user_id, qty_str)
    except Exception:
        _log.exception("Spot BUY failed (user=%s) — aborting entry", st.user_id)
        return

    # 2) 선물 시장가 숏 — 현물 체결 수량을 step에 맞춰 재정렬
    fut_qty = _round_step(st.spot_qty, fut_flt.step_size or step)
    fut_qty_str = _fmt_qty(fut_qty, fut_flt.step_size or step)
    try:
        fut_order = await _place_futures_order(
            client=futures_client,
            api_key=st.api_key,
            api_secret=st.api_secret,
            symbol=params.symbol,
            side="SELL",
            qty_str=fut_qty_str,
            position_side="SHORT",
        )
        st.futures_short_qty = float(fut_order.get("executedQty") or fut_qty)
        st.entry_mark_price = mark_price
        st.entry_ts_ms = int(time.time() * 1000)
        _log.info("Futures SHORT filled (user=%s): qty=%s", st.user_id, fut_qty_str)
        await _persist_state(st)
    except Exception:
        _log.exception(
            "Futures SHORT failed (user=%s) — rolling back spot leg", st.user_id
        )
        await _rollback_spot_leg(st, spot_client=spot_client, qty_str=qty_str)


async def _rollback_spot_leg(
    st: _EngineState, *, spot_client: httpx.AsyncClient, qty_str: str
) -> None:
    """선물 숏 실패 시 고아가 된 현물 롱을 즉시 매도해 델타 노출 해소."""
    params = st.params
    assert params is not None
    try:
        await _place_spot_order(
            client=spot_client,
            api_key=st.spot_api_key,
            api_secret=st.spot_api_secret,
            symbol=params.symbol,
            side="SELL",
            qty_str=qty_str,
        )
        _log.info("Rolled back orphaned spot leg (user=%s): qty=%s", st.user_id, qty_str)
    except Exception:
        _log.exception(
            "CRITICAL: spot rollback failed (user=%s) — manual intervention needed", st.user_id
        )
    finally:
        st.spot_qty = 0.0
        st.futures_short_qty = 0.0
        st.entry_mark_price = 0.0
        st.entry_ts_ms = None
        await _persist_state(st)


async def _unwind_position(
    st: _EngineState,
    *,
    futures_client: httpx.AsyncClient,
    spot_client: httpx.AsyncClient,
) -> None:
    params = st.params
    assert params is not None
    spot_flt = await _get_filter(spot_client, st.spot_base, params.symbol, is_futures=False)
    fut_flt = await _get_filter(futures_client, st.futures_base, params.symbol, is_futures=True)

    spot_slice = _round_step(st.spot_qty / _UNWIND_SLICES, spot_flt.step_size)
    fut_slice = _round_step(st.futures_short_qty / _UNWIND_SLICES, fut_flt.step_size)

    if spot_slice <= 0 and fut_slice <= 0:
        # step보다 작은 잔량 — 전량 한 번에 처리
        spot_slice = st.spot_qty
        fut_slice = st.futures_short_qty

    spot_remaining = st.spot_qty
    fut_remaining = st.futures_short_qty

    for i in range(_UNWIND_SLICES):
        is_last = i == _UNWIND_SLICES - 1
        spot_qty = spot_remaining if is_last else min(spot_slice, spot_remaining)
        fut_qty = fut_remaining if is_last else min(fut_slice, fut_remaining)
        try:
            if spot_qty > 0:
                await _place_spot_order(
                    client=spot_client,
                    api_key=st.spot_api_key,
                    api_secret=st.spot_api_secret,
                    symbol=params.symbol,
                    side="SELL",
                    qty_str=_fmt_qty(spot_qty, spot_flt.step_size),
                )
                spot_remaining -= spot_qty
            if fut_qty > 0:
                await _place_futures_order(
                    client=futures_client,
                    api_key=st.api_key,
                    api_secret=st.api_secret,
                    symbol=params.symbol,
                    side="BUY",
                    qty_str=_fmt_qty(fut_qty, fut_flt.step_size),
                    position_side="SHORT",
                )
                fut_remaining -= fut_qty
            _log.info("Unwind slice %d/%d done (user=%s)", i + 1, _UNWIND_SLICES, st.user_id)
        except Exception:
            _log.exception("Unwind slice %d failed (user=%s)", i + 1, st.user_id)
        if not is_last:
            await asyncio.sleep(_UNWIND_INTERVAL_SEC)

    st.spot_qty = max(spot_remaining, 0.0)
    st.futures_short_qty = max(fut_remaining, 0.0)
    if st.spot_qty == 0.0 and st.futures_short_qty == 0.0:
        st.entry_mark_price = 0.0
        st.entry_ts_ms = None
        st.unrealized_pnl = None
        _log.info("Position fully unwound (user=%s)", st.user_id)
    await _persist_state(st)


# ── 메트릭 (펀딩 수익 / 미실현 손익) ───────────────────────


async def _refresh_metrics(
    st: _EngineState, *, futures_client: httpx.AsyncClient, mark_price: float
) -> None:
    params = st.params
    assert params is not None
    headers = _auth_headers(st.api_key)

    # 누적 펀딩 수익
    try:
        income_params: dict[str, Any] = {
            "incomeType": "FUNDING_FEE",
            "symbol": params.symbol,
            "limit": 1000,
        }
        if st.entry_ts_ms:
            income_params["startTime"] = st.entry_ts_ms
        resp = await futures_client.get(
            "/fapi/v1/income",
            headers=headers,
            params=_signed_params(st.api_secret, income_params),
        )
        resp.raise_for_status()
        rows: list[dict[str, Any]] = resp.json()
        st.accumulated_funding_income = sum(float(r.get("income") or 0) for r in rows)
    except Exception:
        _log.warning("Failed to refresh funding income (user=%s)", st.user_id)

    # 미실현 손익 = 선물 unRealizedProfit + 현물 mark-to-market
    try:
        resp = await futures_client.get(
            "/fapi/v2/positionRisk",
            headers=headers,
            params=_signed_params(st.api_secret, {"symbol": params.symbol}),
        )
        resp.raise_for_status()
        positions: list[dict[str, Any]] = resp.json()
        fut_unrealized = sum(
            float(p.get("unRealizedProfit") or 0)
            for p in positions
            if p.get("positionSide") in ("SHORT", "BOTH")
        )
    except Exception:
        _log.warning("Failed to fetch positionRisk (user=%s)", st.user_id)
        fut_unrealized = 0.0

    spot_unrealized = (
        (mark_price - st.entry_mark_price) * st.spot_qty if st.entry_mark_price > 0 else 0.0
    )
    st.unrealized_pnl = fut_unrealized + spot_unrealized
    await _persist_state(st)


# ── Binance API helpers ────────────────────────────────────


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"X-MBX-APIKEY": api_key}


def _signed_params(api_secret: str, params: dict[str, Any]) -> dict[str, Any]:
    p = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
    query = "&".join(f"{k}={v}" for k, v in p.items())
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return {**p, "signature": sig}


def _fmt_qty(qty: float, step: float) -> str:
    if step > 0 and step < 1:
        decimals = max(0, int(round(-math.log10(step))))
    elif step >= 1:
        decimals = 0
    else:
        decimals = 8
    return f"{qty:.{decimals}f}"


async def _place_spot_order(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    qty_str: str,
) -> dict[str, Any]:
    params = _signed_params(
        api_secret,
        {"symbol": symbol, "side": side, "type": "MARKET", "quantity": qty_str},
    )
    resp = await client.post("/api/v3/order", headers=_auth_headers(api_key), data=params)
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def _place_futures_order(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    qty_str: str,
    position_side: str = "BOTH",
) -> dict[str, Any]:
    params = _signed_params(
        api_secret,
        {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": qty_str,
        },
    )
    resp = await client.post("/fapi/v1/order", headers=_auth_headers(api_key), data=params)
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def _universal_transfer(
    *,
    spot_client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
    transfer_type: str,
    asset: str,
    amount: float,
) -> None:
    params = _signed_params(
        api_secret,
        {"type": transfer_type, "asset": asset, "amount": f"{amount:.2f}"},
    )
    resp = await spot_client.post(
        "/sapi/v1/asset/transfer",
        headers=_auth_headers(api_key),
        data=params,
    )
    resp.raise_for_status()
