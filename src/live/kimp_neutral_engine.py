"""김프 델타-중립 실거래 엔진 — 업비트 현물 롱 + 바이낸스 무기한 숏.

``funding_arbitrage_engine`` 의 라이프사이클/영속화/멀티-replica 안전 패턴을 그대로
미러링하되, 두 레그가 **서로 다른 거래소**(롱=업비트 KRW, 숏=바이낸스 USDT)에 있고
순수 사이징/리밸런스 로직(:mod:`live.kimp_neutral`)을 재사용한다.

핵심 차별점 — 교차거래소 마진 방어
----------------------------------
펀딩 엔진은 마진 위험 시 현물→선물 USDT 이체로 방어한다. 김프 엔진은 롱이 업비트
KRW, 숏이 바이낸스 USDT라 **즉시 이체가 불가능**하다. 대신 마진 위험 시 **북 전체를
대칭 축소**(업비트 매도 + 숏 환매)하여 중립을 유지한 채 마진을 해소한다. 이는
:func:`plan_tick` 에서 신호를 무시하고 목표 북을 축소하는 방식으로 구현된다.

테스트 가능성
-------------
의사결정(:func:`plan_tick`), 주문 적용(:func:`_apply_order`), 상태 직렬화
(:func:`_state_to_dict`/:func:`status_from_dict`)는 거래소/DB 의존이 없거나 주입형
:class:`KimpExecutor` 를 통하므로 단위테스트로 검증한다. 실주문 경로(:class:`LiveExecutor`)는
이 환경에서 E2E 불가하므로 검증된 펀딩 엔진의 저수준 헬퍼를 재사용한다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.schemas import KimpArbitrageParams, KimpArbitrageStatusResponse
from common.crypto import get_crypto_service
from control.models import AccountSnapshot
from control.repo import (
    get_account_snapshot,
    get_binance_credential,
    get_user_profile,
    upsert_account_snapshot,
)

# 서명/주문/필터 헬퍼는 보안 민감 로직이므로 검증된 펀딩 엔진 것을 재사용한다.
from live.funding_arbitrage_engine import (
    _auth_headers,
    _bases_for_env,
    _fmt_qty,
    _get_filter,
    _place_futures_order,
    _round_step,
    _signed_params,
)
from live.kimp_calculator import compute_kimp_snapshot
from live.kimp_history import window_stats
from live.kimp_neutral import (
    HedgeMode,
    KimpQuote,
    LotPair,
    NeutralBook,
    RebalanceAction,
    RebalanceOrder,
    SignalConfig,
    SizingConfig,
    book_deltas,
    plan_rebalance,
    target_book_krw,
)
from upbit.client import UpbitClient

_log = logging.getLogger("llmtrader.kimp_arb")

_POLL_INTERVAL_SEC = 10
_STATUS_STALE_SECONDS = 60
_DERISK_FRACTION = 0.5  # 마진 위험 시 북을 이 비율만큼 축소
_UPBIT_SLIPPAGE_BUF = 1.005  # 시장가 매수 KRW 산정 시 버퍼
_REPLICA_ID = os.environ.get("HOSTNAME") or socket.gethostname()


def _snapshot_key(user_id: str) -> str:
    return f"kimp_arb:{user_id}"


def _control_key(user_id: str) -> str:
    return f"kimp_arb_ctl:{user_id}"


def _is_stale(updated: datetime | None) -> bool:
    if updated is None:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (datetime.now(UTC) - updated).total_seconds() > _STATUS_STALE_SECONDS


def _hedge_mode(params: KimpArbitrageParams) -> HedgeMode:
    return HedgeMode.DELTA if params.hedge_mode == "delta" else HedgeMode.QUANTITY


def _sizing_of(params: KimpArbitrageParams) -> SizingConfig:
    return SizingConfig(
        hedge_mode=_hedge_mode(params),
        leverage=params.leverage,
        upbit_taker_fee=params.upbit_taker_fee,
        binance_taker_fee=params.binance_taker_fee,
    )


def _signal_of(params: KimpArbitrageParams) -> SignalConfig:
    return SignalConfig(
        gross_cap_krw=params.gross_cap_krw,
        full_build_z=params.full_build_z,
        flat_z=params.flat_z,
    )


def compute_zscore(kimp: float, mean: float | None, std: float | None) -> float | None:
    """김프 z-score. 통계 없거나 무분산이면 None."""
    if mean is None or std is None or std <= 0:
        return None
    return (kimp - mean) / std


# ── 실행 추상화 (주입형) ───────────────────────────────────


class KimpExecutor(Protocol):
    """틱 실행에 필요한 외부 I/O. 테스트에서 페이크로 대체된다."""

    async def fetch_quote(self, symbol: str) -> KimpQuote: ...

    async def fetch_zscore(self, symbol: str, window_days: int) -> float | None: ...

    async def fetch_margin_ratio(self) -> float | None: ...

    async def buy_upbit(self, symbol: str, qty: float, price_krw: float) -> float: ...

    async def sell_upbit(self, symbol: str, qty: float) -> float: ...

    async def open_short(self, symbol: str, qty: float) -> float: ...

    async def cover_short(self, symbol: str, qty: float) -> float: ...


# ── 엔진 상태 ──────────────────────────────────────────────


@dataclass
class _KimpEngineState:
    user_id: str
    running: bool = False
    symbol: str | None = None
    upbit_long_qty: float = 0.0
    binance_short_qty: float = 0.0
    binance_margin_usdt: float = 0.0
    entry_quote: KimpQuote | None = None
    current_kimp: float | None = None
    current_z: float | None = None
    target_notional_krw: float = 0.0
    current_notional_krw: float = 0.0
    fx_hedge_usd: float = 0.0
    coin_delta_qty: float = 0.0
    price_delta_krw: float = 0.0
    mtm_pnl_krw: float = 0.0
    accumulated_fee_krw: float = 0.0
    binance_margin_ratio: float | None = None
    last_rebalance_ts: datetime | None = None
    params: KimpArbitrageParams | None = None
    last_error: str | None = None
    owner: str = _REPLICA_ID
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    upbit_access: str = ""
    upbit_secret: str = ""
    binance_key: str = ""
    binance_secret: str = ""
    futures_base: str = "https://fapi.binance.com"
    is_testnet: bool = False
    session_maker: async_sessionmaker[AsyncSession] | None = field(default=None, repr=False)
    _prev_quote: KimpQuote | None = field(default=None, repr=False, compare=False)
    _tick_count: int = 0
    _task: asyncio.Task[None] | None = field(default=None, repr=False, compare=False)

    def book(self) -> NeutralBook:
        entry = self.entry_quote or KimpQuote(
            symbol=self.symbol or "?", upbit_krw=1.0, binance_usdt=1.0, usd_krw=1.0
        )
        return NeutralBook(
            symbol=self.symbol or "?",
            upbit_long_qty=self.upbit_long_qty,
            binance_short_qty=self.binance_short_qty,
            entry=entry,
            binance_margin_usdt=self.binance_margin_usdt,
            hedge_mode=_hedge_mode(self.params) if self.params else HedgeMode.QUANTITY,
        )


_engines: dict[str, _KimpEngineState] = {}


# ── 순수 의사결정 ──────────────────────────────────────────


@dataclass(frozen=True)
class TickDecision:
    z: float | None
    target_notional_krw: float
    current_notional_krw: float
    order: RebalanceOrder
    derisk: bool


def plan_tick(
    *,
    upbit_long_qty: float,
    quote: KimpQuote,
    z: float | None,
    margin_ratio: float | None,
    params: KimpArbitrageParams,
) -> TickDecision:
    """현재 북/시세/z/마진으로 이번 틱의 대칭 리밸런스 주문을 결정한다(순수).

    - z 없음(통계 부족): 보유 유지(목표=현재).
    - 마진 위험: 신호를 무시하고 북을 ``_DERISK_FRACTION`` 만큼 축소(중립 유지).
    """
    sizing = _sizing_of(params)
    signal = _signal_of(params)
    lots = LotPair()  # 라이브 실행 측에서 거래소 필터로 라운딩
    current_notional = upbit_long_qty * quote.upbit_krw

    target = current_notional if z is None else target_book_krw(z, signal)

    derisk = (
        margin_ratio is not None
        and margin_ratio >= params.margin_alert_ratio
        and current_notional > 0.0
    )
    if derisk:
        target = min(target, current_notional * (1.0 - _DERISK_FRACTION))

    order = plan_rebalance(current_notional, target, quote, lots, sizing)
    return TickDecision(
        z=z,
        target_notional_krw=target,
        current_notional_krw=current_notional,
        order=order,
        derisk=derisk,
    )


# ── 주문 적용 (주입형 executor) ────────────────────────────


async def _apply_order(
    st: _KimpEngineState, order: RebalanceOrder, quote: KimpQuote, executor: KimpExecutor
) -> None:
    """대칭 리밸런스 주문을 두 거래소에 실행하고 상태를 갱신한다.

    두 레그를 항상 같은 코인 수량 방향으로 움직여 네이키드 노출을 만들지 않는다.
    """
    if order.action is RebalanceAction.HOLD or order.upbit_qty <= 0.0:
        return
    assert st.symbol is not None and st.params is not None
    sym = st.symbol
    lev = st.params.leverage

    was_flat = st.upbit_long_qty <= 0.0 and st.binance_short_qty <= 0.0

    if order.action is RebalanceAction.SCALE_UP:
        filled_u = await executor.buy_upbit(sym, order.upbit_qty, quote.upbit_krw)
        filled_b = await executor.open_short(sym, order.binance_qty)
        st.upbit_long_qty += filled_u
        st.binance_short_qty += filled_b
        st.binance_margin_usdt += filled_b * quote.binance_usdt / lev
    else:  # SCALE_DOWN
        sell_u = min(order.upbit_qty, st.upbit_long_qty)
        cover_b = min(order.binance_qty, st.binance_short_qty)
        filled_u = await executor.sell_upbit(sym, sell_u)
        filled_b = await executor.cover_short(sym, cover_b)
        st.upbit_long_qty = max(0.0, st.upbit_long_qty - filled_u)
        st.binance_short_qty = max(0.0, st.binance_short_qty - filled_b)
        st.binance_margin_usdt = max(
            0.0, st.binance_margin_usdt - filled_b * quote.binance_usdt / lev
        )

    fee = (
        order.upbit_qty * quote.upbit_krw * st.params.upbit_taker_fee
        + order.binance_qty * quote.binance_usdt * quote.usd_krw * st.params.binance_taker_fee
    )
    st.accumulated_fee_krw += fee
    st.last_rebalance_ts = datetime.now(UTC)

    # 평탄→보유 전환 시 진입 시세를 기록(MTM/FX 기준). 완전 청산 시 초기화.
    if was_flat and st.upbit_long_qty > 0.0:
        st.entry_quote = quote
    if st.upbit_long_qty <= 0.0 and st.binance_short_qty <= 0.0:
        st.entry_quote = None
        st.binance_margin_usdt = 0.0


# ── 메트릭/상태 ────────────────────────────────────────────


def _refresh_metrics(st: _KimpEngineState, quote: KimpQuote) -> None:
    """현재 시세 기준 델타/FX/MTM 메트릭을 갱신한다(순수 계산)."""
    st.current_kimp = quote.kimp
    st.current_notional_krw = st.upbit_long_qty * quote.upbit_krw

    if st.upbit_long_qty > 0.0 or st.binance_short_qty > 0.0:
        deltas = book_deltas(st.book(), quote)
        st.coin_delta_qty = deltas.coin_delta_qty
        st.price_delta_krw = deltas.price_delta_krw
        st.fx_hedge_usd = deltas.fx_hedge_usd
    else:
        st.coin_delta_qty = 0.0
        st.price_delta_krw = 0.0
        st.fx_hedge_usd = 0.0

    # MTM: 직전 틱 대비 보유 북의 가격 손익 누적.
    prev = st._prev_quote
    if prev is not None and (st.upbit_long_qty > 0.0 or st.binance_short_qty > 0.0):
        d_long = st.upbit_long_qty * (quote.upbit_krw - prev.upbit_krw)
        d_short = st.binance_short_qty * (prev.binance_usdt - quote.binance_usdt) * quote.usd_krw
        st.mtm_pnl_krw += d_long + d_short
    st._prev_quote = quote


def get_engine_status(user_id: str) -> KimpArbitrageStatusResponse:
    st = _engines.get(user_id)
    if st is None:
        return KimpArbitrageStatusResponse(running=False)
    return KimpArbitrageStatusResponse(
        running=st.running,
        symbol=st.symbol,
        upbit_long_qty=st.upbit_long_qty or None,
        binance_short_qty=st.binance_short_qty or None,
        kimp_pct=st.current_kimp,
        zscore=st.current_z,
        target_notional_krw=st.target_notional_krw or None,
        current_notional_krw=st.current_notional_krw or None,
        fx_hedge_usd=st.fx_hedge_usd or None,
        coin_delta_qty=st.coin_delta_qty,
        price_delta_krw=st.price_delta_krw,
        unrealized_pnl_krw=st.mtm_pnl_krw,
        accumulated_fee_krw=st.accumulated_fee_krw,
        binance_margin_ratio=st.binance_margin_ratio,
        last_rebalance_ts=st.last_rebalance_ts,
        params=st.params,
        last_error=st.last_error,
    )


def _state_to_dict(st: _KimpEngineState) -> dict[str, Any]:
    return {
        "running": st.running,
        "owner": st.owner,
        "instance_id": st.instance_id,
        "symbol": st.symbol,
        "upbit_long_qty": st.upbit_long_qty,
        "binance_short_qty": st.binance_short_qty,
        "binance_margin_usdt": st.binance_margin_usdt,
        "current_kimp": st.current_kimp,
        "current_z": st.current_z,
        "target_notional_krw": st.target_notional_krw,
        "current_notional_krw": st.current_notional_krw,
        "fx_hedge_usd": st.fx_hedge_usd,
        "coin_delta_qty": st.coin_delta_qty,
        "price_delta_krw": st.price_delta_krw,
        "mtm_pnl_krw": st.mtm_pnl_krw,
        "accumulated_fee_krw": st.accumulated_fee_krw,
        "binance_margin_ratio": st.binance_margin_ratio,
        "last_rebalance_ts": st.last_rebalance_ts.isoformat() if st.last_rebalance_ts else None,
        "last_error": st.last_error,
        "params": st.params.model_dump() if st.params else None,
    }


def status_from_dict(
    d: dict[str, Any], *, running_override: bool | None = None
) -> KimpArbitrageStatusResponse:
    """영속화된 스냅샷 dict → 상태 응답(순수). 멀티-replica 조회에 사용."""
    params = None
    if d.get("params"):
        try:
            params = KimpArbitrageParams(**d["params"])
        except Exception:  # noqa: BLE001
            params = None
    last_dt = None
    if d.get("last_rebalance_ts"):
        try:
            last_dt = datetime.fromisoformat(d["last_rebalance_ts"])
        except Exception:  # noqa: BLE001
            last_dt = None
    running = bool(d.get("running")) if running_override is None else running_override
    return KimpArbitrageStatusResponse(
        running=running,
        symbol=d.get("symbol"),
        upbit_long_qty=(d.get("upbit_long_qty") or None),
        binance_short_qty=(d.get("binance_short_qty") or None),
        kimp_pct=d.get("current_kimp"),
        zscore=d.get("current_z"),
        target_notional_krw=(d.get("target_notional_krw") or None),
        current_notional_krw=(d.get("current_notional_krw") or None),
        fx_hedge_usd=(d.get("fx_hedge_usd") or None),
        coin_delta_qty=d.get("coin_delta_qty"),
        price_delta_krw=d.get("price_delta_krw"),
        unrealized_pnl_krw=d.get("mtm_pnl_krw"),
        accumulated_fee_krw=float(d.get("accumulated_fee_krw") or 0.0),
        binance_margin_ratio=d.get("binance_margin_ratio"),
        last_rebalance_ts=last_dt,
        params=params,
        last_error=d.get("last_error"),
    )


async def _persist_state(st: _KimpEngineState) -> None:
    if st.session_maker is None:
        return
    try:
        async with st.session_maker() as session:
            await upsert_account_snapshot(
                session, key=_snapshot_key(st.user_id), data_json=_state_to_dict(st)
            )
            await session.commit()
    except Exception:
        _log.exception("Failed to persist kimp-arb state for user=%s", st.user_id)


async def _set_desired_running(
    session_maker: async_sessionmaker[AsyncSession],
    user_id: str,
    value: bool,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    data: dict[str, Any] = {"desired_running": bool(value)}
    if extra:
        data.update(extra)
    async with session_maker() as session:
        await upsert_account_snapshot(session, key=_control_key(user_id), data_json=data)
        await session.commit()


async def _get_control_state(
    session_maker: async_sessionmaker[AsyncSession], user_id: str
) -> dict[str, Any]:
    async with session_maker() as session:
        snap = await get_account_snapshot(session, key=_control_key(user_id))
    if not snap or not snap.data_json:
        return {}
    return dict(snap.data_json)


async def get_engine_status_persisted(
    session: AsyncSession, user_id: str
) -> KimpArbitrageStatusResponse:
    """모든 replica에서 일관된 상태를 반환한다(인메모리 우선, 아니면 스냅샷)."""
    desired = True
    ctl_instance: str | None = None
    ctl = await get_account_snapshot(session, key=_control_key(user_id))
    if ctl and ctl.data_json:
        desired = bool(ctl.data_json.get("desired_running", True))
        ctl_instance = ctl.data_json.get("instance_id")

    st = _engines.get(user_id)
    if (
        st is not None
        and st.running
        and desired
        and (ctl_instance is None or ctl_instance == st.instance_id)
    ):
        return get_engine_status(user_id)

    snap = await get_account_snapshot(session, key=_snapshot_key(user_id))
    if not snap or not snap.data_json:
        return KimpArbitrageStatusResponse(running=False)
    running = bool(snap.data_json.get("running")) and desired and not _is_stale(snap.updated_at)
    return status_from_dict(snap.data_json, running_override=running)


# ── 라이브 실행 어댑터 ─────────────────────────────────────


class LiveExecutor:
    """실거래용 :class:`KimpExecutor`. 업비트 + 바이낸스 무기한.

    E2E 검증은 실 키가 필요하므로 이 환경에서 수행 불가. 저수준 서명/주문 헬퍼는
    검증된 펀딩 엔진 것을 재사용한다.
    """

    def __init__(self, st: _KimpEngineState) -> None:
        self._st = st
        self._fut = httpx.AsyncClient(base_url=st.futures_base, timeout=10.0)
        self._upbit = UpbitClient(access_key=st.upbit_access, secret_key=st.upbit_secret)
        self._pos_side = "BOTH"

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._fut.aclose()
        with contextlib.suppress(Exception):
            await self._upbit.aclose()

    async def fetch_quote(self, symbol: str) -> KimpQuote:
        snap = await compute_kimp_snapshot([symbol])
        row = next((r for r in snap.rows if r.symbol == symbol), None)
        if row is None:
            raise RuntimeError(f"{symbol} 김프 시세를 가져오지 못했습니다: {snap.errors[:2]}")
        return KimpQuote(
            symbol=symbol,
            upbit_krw=row.upbit_krw_price,
            binance_usdt=row.binance_usdt_price,
            usd_krw=row.usd_krw_rate,
        )

    async def fetch_zscore(self, symbol: str, window_days: int) -> float | None:
        if self._st.session_maker is None:
            return None
        async with self._st.session_maker() as session:
            stats = await window_stats(session, symbol, window_days)
        last = stats.get("last")
        if last is None:
            return None
        return compute_zscore(float(last), stats.get("mean"), stats.get("std"))

    async def fetch_margin_ratio(self) -> float | None:
        if self._st.is_testnet:
            return None
        try:
            resp = await self._fut.get(
                "/fapi/v2/account",
                headers=_auth_headers(self._st.binance_key),
                params=_signed_params(self._st.binance_secret, {}),
            )
            resp.raise_for_status()
            acc = resp.json()
        except Exception:
            return None
        total = float(acc.get("totalMarginBalance") or 0)
        maint = float(acc.get("totalMaintMargin") or 0)
        return (maint / total) if total > 0 else None

    async def buy_upbit(self, symbol: str, qty: float, price_krw: float) -> float:
        krw = round(qty * price_krw * _UPBIT_SLIPPAGE_BUF)
        await self._upbit.place_market_buy_krw(f"KRW-{symbol}", krw)
        return qty

    async def sell_upbit(self, symbol: str, qty: float) -> float:
        await self._upbit.place_market_sell(f"KRW-{symbol}", qty)
        return qty

    async def _binance_qty_str(self, symbol: str, qty: float) -> str:
        flt = await _get_filter(self._fut, self._st.futures_base, f"{symbol}USDT", is_futures=True)
        rounded = _round_step(qty, flt.step_size)
        return _fmt_qty(rounded, flt.step_size)

    async def open_short(self, symbol: str, qty: float) -> float:
        qty_str = await self._binance_qty_str(symbol, qty)
        await _place_futures_order(
            client=self._fut,
            api_key=self._st.binance_key,
            api_secret=self._st.binance_secret,
            symbol=f"{symbol}USDT",
            side="SELL",
            qty_str=qty_str,
            position_side=self._pos_side,
        )
        return float(qty_str)

    async def cover_short(self, symbol: str, qty: float) -> float:
        qty_str = await self._binance_qty_str(symbol, qty)
        await _place_futures_order(
            client=self._fut,
            api_key=self._st.binance_key,
            api_secret=self._st.binance_secret,
            symbol=f"{symbol}USDT",
            side="BUY",
            qty_str=qty_str,
            position_side=self._pos_side,
            reduce_only=True,
        )
        return float(qty_str)


# ── 틱/루프 ────────────────────────────────────────────────


async def _tick(st: _KimpEngineState, executor: KimpExecutor) -> None:
    assert st.params is not None and st.symbol is not None
    st._tick_count += 1

    quote = await executor.fetch_quote(st.symbol)
    z = await executor.fetch_zscore(st.symbol, st.params.z_window_days)
    st.current_z = z

    has_position = st.upbit_long_qty > 0.0 or st.binance_short_qty > 0.0
    margin_ratio = await executor.fetch_margin_ratio() if has_position else None
    st.binance_margin_ratio = margin_ratio

    decision = plan_tick(
        upbit_long_qty=st.upbit_long_qty,
        quote=quote,
        z=z,
        margin_ratio=margin_ratio,
        params=st.params,
    )
    st.target_notional_krw = decision.target_notional_krw

    if decision.derisk:
        _log.warning(
            "Margin de-risk (user=%s): ratio=%.2f → scaling book down", st.user_id, margin_ratio
        )

    try:
        await _apply_order(st, decision.order, quote, executor)
        st.last_error = None
    except Exception as exc:  # noqa: BLE001
        st.last_error = f"리밸런스 실행 실패: {exc}"
        _log.exception("Rebalance execution failed (user=%s)", st.user_id)

    _refresh_metrics(st, quote)


async def _engine_loop(st: _KimpEngineState) -> None:
    executor = LiveExecutor(st)
    try:
        while st.running:
            try:
                await _tick(st, executor)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception(
                    "Tick error (user=%s) — retry in %ds", st.user_id, _POLL_INTERVAL_SEC
                )

            with contextlib.suppress(Exception):
                await _persist_state(st)

            if st.session_maker is not None:
                with contextlib.suppress(Exception):
                    ctl = await _get_control_state(st.session_maker, st.user_id)
                    desired = bool(ctl.get("desired_running", True))
                    ctl_instance = ctl.get("instance_id")
                    superseded = ctl_instance is not None and ctl_instance != st.instance_id
                    if not desired or superseded:
                        _log.info(
                            "Self-stopping (user=%s): desired=%s superseded=%s",
                            st.user_id,
                            desired,
                            superseded,
                        )
                        st.running = False
                        if not superseded:
                            with contextlib.suppress(Exception):
                                await _liquidate_all(st, executor)
                                await _persist_state(st)
                        break

            await asyncio.sleep(_POLL_INTERVAL_SEC)
    finally:
        await executor.aclose()


async def _liquidate_all(st: _KimpEngineState, executor: KimpExecutor) -> None:
    """보유 북을 대칭 청산한다(업비트 매도 + 숏 환매). 멱등적."""
    if st.symbol is None:
        return
    if st.upbit_long_qty > 0.0:
        with contextlib.suppress(Exception):
            await executor.sell_upbit(st.symbol, st.upbit_long_qty)
    if st.binance_short_qty > 0.0:
        with contextlib.suppress(Exception):
            await executor.cover_short(st.symbol, st.binance_short_qty)
    st.upbit_long_qty = 0.0
    st.binance_short_qty = 0.0
    st.binance_margin_usdt = 0.0
    st.entry_quote = None
    st.current_notional_krw = 0.0


# ── 퍼블릭 라이프사이클 ────────────────────────────────────


async def start_engine(  # noqa: PLR0913 — credentials/env are distinct required inputs
    *,
    user_id: str,
    params: KimpArbitrageParams,
    upbit_access: str,
    upbit_secret: str,
    binance_key: str,
    binance_secret: str,
    is_testnet: bool,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    existing = _engines.get(user_id)
    if existing and existing.running:
        _log.warning("Kimp engine already running for user=%s — ignoring start", user_id)
        return

    _, futures_base = _bases_for_env(is_testnet)
    st = _KimpEngineState(
        user_id=user_id,
        running=True,
        symbol=params.symbol,
        params=params,
        upbit_access=upbit_access,
        upbit_secret=upbit_secret,
        binance_key=binance_key,
        binance_secret=binance_secret,
        futures_base=futures_base,
        is_testnet=is_testnet,
        session_maker=session_maker,
    )
    _engines[user_id] = st

    if session_maker is not None:
        await _set_desired_running(
            session_maker,
            user_id,
            True,
            extra={
                "env": "testnet" if is_testnet else "mainnet",
                "is_testnet": is_testnet,
                "params": params.model_dump(),
                "symbol": params.symbol,
                "instance_id": st.instance_id,
            },
        )
        await _persist_state(st)

    st._task = asyncio.create_task(_engine_loop(st), name=f"kimp_arb_{user_id}")
    _log.info(
        "Kimp arbitrage engine started: user=%s symbol=%s testnet=%s replica=%s",
        user_id,
        params.symbol,
        is_testnet,
        _REPLICA_ID,
    )


async def stop_engine(
    user_id: str,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if session_maker is not None:
        with contextlib.suppress(Exception):
            await _set_desired_running(session_maker, user_id, False)

    st = _engines.get(user_id)
    if st is None:
        if session_maker is not None:
            with contextlib.suppress(Exception):
                async with session_maker() as session:
                    snap = await get_account_snapshot(session, key=_snapshot_key(user_id))
                    if snap and snap.data_json and snap.data_json.get("running"):
                        data = dict(snap.data_json)
                        data["running"] = False
                        await upsert_account_snapshot(
                            session, key=_snapshot_key(user_id), data_json=data
                        )
                        await session.commit()
        return

    st.running = False
    if st._task and not st._task.done():
        st._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await st._task

    executor = LiveExecutor(st)
    try:
        with contextlib.suppress(Exception):
            await asyncio.shield(_liquidate_all(st, executor))
    finally:
        await executor.aclose()
    with contextlib.suppress(Exception):
        await _persist_state(st)
    _log.info("Kimp arbitrage engine stopped: user=%s replica=%s", user_id, _REPLICA_ID)


async def restore_engines_on_startup(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """부팅 시 desired_running=true인 김프 엔진을 자동 복원한다."""
    crypto = get_crypto_service()
    rows: list[AccountSnapshot] = []
    try:
        async with session_maker() as session:
            result = await session.execute(
                select(AccountSnapshot).where(AccountSnapshot.key.like("kimp_arb_ctl:%"))
            )
            rows = list(result.scalars().all())
    except Exception:
        _log.warning("Kimp auto-restore: DB query failed")
        return

    crypto = get_crypto_service()
    for snap in rows:
        data = snap.data_json or {}
        if not data.get("desired_running"):
            continue
        user_id = snap.key.split(":", 1)[1]
        if user_id in _engines and _engines[user_id].running:
            continue
        params_raw = data.get("params")
        if not params_raw:
            continue
        try:
            params = KimpArbitrageParams(**params_raw)
            is_testnet = bool(data.get("is_testnet"))
            env = "testnet" if is_testnet else "mainnet"
            async with session_maker() as session:
                profile = await get_user_profile(session, user_id=user_id)
                cred = await get_binance_credential(session, user_id=user_id, env=env)
            if profile is None or cred is None:
                continue
            if not profile.upbit_api_key_enc or not profile.upbit_api_secret_enc:
                continue
            await start_engine(
                user_id=user_id,
                params=params,
                upbit_access=crypto.decrypt(profile.upbit_api_key_enc),
                upbit_secret=crypto.decrypt(profile.upbit_api_secret_enc),
                binance_key=crypto.decrypt(cred.api_key_enc),
                binance_secret=crypto.decrypt(cred.api_secret_enc),
                is_testnet=is_testnet,
                session_maker=session_maker,
            )
            _log.info("Auto-restored kimp engine user=%s symbol=%s", user_id, params.symbol)
        except Exception:
            _log.exception("Kimp auto-restore failed for key=%s", snap.key)


__all__ = [
    "KimpExecutor",
    "LiveExecutor",
    "TickDecision",
    "compute_zscore",
    "plan_tick",
    "get_engine_status",
    "get_engine_status_persisted",
    "status_from_dict",
    "start_engine",
    "stop_engine",
    "restore_engines_on_startup",
]
