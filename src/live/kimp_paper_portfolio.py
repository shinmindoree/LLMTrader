"""김프 페이퍼 자동운용 포트폴리오 — 랭킹 상위 N 종목 동시 페이퍼 운용.

단일 라이브/페이퍼 엔진(:mod:`live.kimp_neutral_engine`)을 그대로 두고, 그 위에
얹는 **추가 계층**이다. 유니버스를 백테스트로 랭킹(:func:`run_universe_backtest`)해
상위 ``top_n`` 종목을 각각 :class:`_KimpEngineState`(``paper=True``) 슬롯으로 만들고,
하나의 루프에서 매 틱 :func:`_tick` 로 모의 운용한다. ``rerank_hours`` 마다 재랭킹해
슬롯을 교체한다.

페이퍼 전용이라 실주문·거래소 키·교차거래소 마진 위험이 없어 한 태스크에서 N개
북을 안전하게 굴릴 수 있다. 의사결정/사이징/PnL 거동은 단일 엔진과 동일한 순수
로직을 재사용하므로 결과 일관성이 보장된다.

영속화: 상태 스냅샷(``kimp_paper_pf:{user_id}``)으로 타 replica 조회를 지원하고,
컨트롤 키(``kimp_paper_pf_ctl:{user_id}``)로 재시작 후 자동 복원한다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.schemas import (
    KimpArbitrageParams,
    KimpPaperPortfolioParams,
    KimpPaperPortfolioStatus,
    KimpPaperSlotStatus,
)
from control.models import AccountSnapshot
from control.repo import get_account_snapshot, upsert_account_snapshot
from live.kimp_backtest_data import run_universe_backtest
from live.kimp_neutral import HedgeMode
from live.kimp_neutral_backtest import BacktestConfig
from live.kimp_neutral_engine import (
    _REPLICA_ID,
    PaperExecutor,
    _KimpEngineState,
    _liquidate_all,
    _tick,
)
from live.kimp_universe import get_kimp_universe

_log = logging.getLogger("llmtrader.kimp_paper_pf")

_POLL_INTERVAL_SEC = 10
_STALE_SECONDS = 120


def _pf_snapshot_key(user_id: str) -> str:
    return f"kimp_paper_pf:{user_id}"


def _pf_control_key(user_id: str) -> str:
    return f"kimp_paper_pf_ctl:{user_id}"


def _is_stale(updated: datetime | None) -> bool:
    if updated is None:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (datetime.now(UTC) - updated).total_seconds() > _STALE_SECONDS


def _slot_params(symbol: str, pf: KimpPaperPortfolioParams) -> KimpArbitrageParams:
    """포트폴리오 파라미터로 한 종목의 페이퍼 엔진 파라미터를 만든다."""
    return KimpArbitrageParams(
        symbol=symbol,
        env="mainnet",
        mode="paper",
        gross_cap_krw=pf.capital_per_slot_krw,
        full_build_z=pf.full_build_z,
        flat_z=pf.flat_z,
        hedge_mode=pf.hedge_mode,
        leverage=pf.leverage,
        z_window_days=pf.z_window_days,
        upbit_taker_fee=pf.upbit_taker_fee,
        binance_taker_fee=pf.binance_taker_fee,
    )


def _make_slot(
    user_id: str,
    symbol: str,
    pf: KimpPaperPortfolioParams,
    session_maker: async_sessionmaker[AsyncSession] | None,
) -> _KimpEngineState:
    """페이퍼 슬롯(:class:`_KimpEngineState`)을 만든다(주문/키 없음)."""
    return _KimpEngineState(
        user_id=user_id,
        running=True,
        symbol=symbol,
        params=_slot_params(symbol, pf),
        paper=True,
        session_maker=session_maker,
    )


def _config_of(pf: KimpPaperPortfolioParams) -> BacktestConfig:
    return BacktestConfig(
        gross_cap_krw=pf.capital_per_slot_krw,
        full_build_z=pf.full_build_z,
        flat_z=pf.flat_z,
        hedge_mode=HedgeMode.DELTA if pf.hedge_mode == "delta" else HedgeMode.QUANTITY,
        leverage=pf.leverage,
        z_window=pf.rank_z_window_points,
        upbit_taker_fee=pf.upbit_taker_fee,
        binance_taker_fee=pf.binance_taker_fee,
    )


@dataclass
class _PortfolioState:
    user_id: str
    params: KimpPaperPortfolioParams
    running: bool = False
    slots: dict[str, _KimpEngineState] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    last_rank_ts: datetime | None = None
    last_error: str | None = None
    owner: str = _REPLICA_ID
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    session_maker: async_sessionmaker[AsyncSession] | None = field(default=None, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, repr=False, compare=False)


_portfolios: dict[str, _PortfolioState] = {}


# ── 순수 헬퍼 ──────────────────────────────────────────────


def _desired_symbols(
    ranked: list[tuple[str, float | None]], top_n: int
) -> list[str]:
    """랭킹 결과에서 점수가 유효한 상위 ``top_n`` 심볼을 고른다(순수)."""
    out: list[str] = []
    for sym, score in ranked:
        if score is None:
            continue
        out.append(sym)
        if len(out) >= top_n:
            break
    return out


def _slot_status(st: _KimpEngineState, score: float | None) -> KimpPaperSlotStatus:
    return KimpPaperSlotStatus(
        symbol=st.symbol or "?",
        score=score,
        kimp_pct=st.current_kimp,
        zscore=st.current_z,
        target_notional_krw=st.target_notional_krw or None,
        current_notional_krw=st.current_notional_krw or None,
        upbit_long_qty=st.upbit_long_qty or None,
        binance_short_qty=st.binance_short_qty or None,
        unrealized_pnl_krw=st.mtm_pnl_krw,
        accumulated_fee_krw=st.accumulated_fee_krw,
        last_rebalance_ts=st.last_rebalance_ts,
        last_error=st.last_error,
    )


def build_status(pf: _PortfolioState) -> KimpPaperPortfolioStatus:
    """포트폴리오 상태 응답을 만든다(순수 집계)."""
    slots = [
        _slot_status(st, pf.scores.get(sym))
        for sym, st in sorted(
            pf.slots.items(), key=lambda kv: pf.scores.get(kv[0], float("-inf")), reverse=True
        )
    ]
    total_notional = sum(s.current_notional_krw or 0.0 for s in slots)
    total_pnl = sum(s.unrealized_pnl_krw or 0.0 for s in slots)
    total_fee = sum(s.accumulated_fee_krw for s in slots)
    next_rank = (
        pf.last_rank_ts + timedelta(hours=pf.params.rerank_hours)
        if pf.last_rank_ts is not None
        else None
    )
    return KimpPaperPortfolioStatus(
        running=pf.running,
        top_n=pf.params.top_n,
        capital_per_slot_krw=pf.params.capital_per_slot_krw,
        n_slots=len(slots),
        total_notional_krw=total_notional,
        total_unrealized_pnl_krw=total_pnl,
        total_fee_krw=total_fee,
        rerank_hours=pf.params.rerank_hours,
        last_rank_ts=pf.last_rank_ts,
        next_rank_ts=next_rank,
        slots=slots,
        params=pf.params,
        last_error=pf.last_error,
    )


def _status_to_dict(status: KimpPaperPortfolioStatus) -> dict[str, Any]:
    return status.model_dump(mode="json")


# ── 랭킹/슬롯 동기화 ───────────────────────────────────────


async def _rank_universe(pf: _PortfolioState) -> list[tuple[str, float | None]]:
    """유니버스 상위 후보를 백테스트로 랭킹한다."""
    universe = list(await get_kimp_universe())[: pf.params.candidate_limit]
    if not universe:
        return []
    results = await run_universe_backtest(
        universe,
        days=pf.params.rank_days,
        rate_mode="usdt",
        include_funding=True,
        config=_config_of(pf.params),
        concurrency=4,
    )
    return [(it.symbol, (it.score if it.metrics is not None else None)) for it in results]


async def _sync_slots(pf: _PortfolioState, desired: list[str]) -> None:
    """원하는 심볼 집합에 맞춰 슬롯을 추가/청산한다."""
    desired_set = set(desired)
    # 빠진 슬롯 청산·제거
    for sym in list(pf.slots.keys()):
        if sym not in desired_set:
            st = pf.slots.pop(sym)
            st.running = False
            with contextlib.suppress(Exception):
                await _liquidate_all(st, PaperExecutor(st))
            _log.info("Paper PF drop slot user=%s symbol=%s", pf.user_id, sym)
    # 새 슬롯 추가
    for sym in desired:
        if sym not in pf.slots:
            pf.slots[sym] = _make_slot(pf.user_id, sym, pf.params, pf.session_maker)
            _log.info("Paper PF add slot user=%s symbol=%s", pf.user_id, sym)


async def _rerank_and_sync(pf: _PortfolioState) -> None:
    ranked = await _rank_universe(pf)
    pf.scores = {sym: score for sym, score in ranked if score is not None}
    desired = _desired_symbols(ranked, pf.params.top_n)
    if desired:
        await _sync_slots(pf, desired)
    pf.last_rank_ts = datetime.now(UTC)


# ── 영속화 ─────────────────────────────────────────────────


async def _persist(pf: _PortfolioState) -> None:
    if pf.session_maker is None:
        return
    try:
        async with pf.session_maker() as session:
            await upsert_account_snapshot(
                session,
                key=_pf_snapshot_key(pf.user_id),
                data_json=_status_to_dict(build_status(pf)),
            )
            await session.commit()
    except Exception:
        _log.exception("Paper PF persist failed user=%s", pf.user_id)


async def _set_control(
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
        await upsert_account_snapshot(session, key=_pf_control_key(user_id), data_json=data)
        await session.commit()


async def _get_control(
    session_maker: async_sessionmaker[AsyncSession], user_id: str
) -> dict[str, Any]:
    async with session_maker() as session:
        snap = await get_account_snapshot(session, key=_pf_control_key(user_id))
    if not snap or not snap.data_json:
        return {}
    return dict(snap.data_json)


# ── 루프 ───────────────────────────────────────────────────


async def _portfolio_loop(pf: _PortfolioState) -> None:
    try:
        await _rerank_and_sync(pf)
    except Exception as exc:  # noqa: BLE001
        pf.last_error = f"초기 랭킹 실패: {exc}"
        _log.exception("Paper PF initial rank failed user=%s", pf.user_id)

    while pf.running:
        for sym, st in list(pf.slots.items()):
            try:
                await _tick(st, PaperExecutor(st))
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("Paper PF tick error user=%s symbol=%s", pf.user_id, sym)

        with contextlib.suppress(Exception):
            await _persist(pf)

        # 재랭킹 시점 도달 시 슬롯 교체.
        due = pf.last_rank_ts is None or (
            datetime.now(UTC) - pf.last_rank_ts
        ).total_seconds() >= pf.params.rerank_hours * 3600.0
        if due:
            try:
                await _rerank_and_sync(pf)
                pf.last_error = None
            except Exception as exc:  # noqa: BLE001
                pf.last_error = f"재랭킹 실패: {exc}"
                _log.exception("Paper PF rerank failed user=%s", pf.user_id)

        # 다른 replica/정지 신호 확인.
        if pf.session_maker is not None:
            with contextlib.suppress(Exception):
                ctl = await _get_control(pf.session_maker, pf.user_id)
                desired = bool(ctl.get("desired_running", True))
                ctl_instance = ctl.get("instance_id")
                superseded = ctl_instance is not None and ctl_instance != pf.instance_id
                if not desired or superseded:
                    pf.running = False
                    if not superseded:
                        with contextlib.suppress(Exception):
                            await _liquidate_slots(pf)
                            await _persist(pf)
                    break

        await asyncio.sleep(_POLL_INTERVAL_SEC)


async def _liquidate_slots(pf: _PortfolioState) -> None:
    for st in pf.slots.values():
        st.running = False
        with contextlib.suppress(Exception):
            await _liquidate_all(st, PaperExecutor(st))


# ── 라이프사이클 ───────────────────────────────────────────


async def start_portfolio(
    *,
    user_id: str,
    params: KimpPaperPortfolioParams,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    existing = _portfolios.get(user_id)
    if existing and existing.running:
        _log.warning("Paper PF already running user=%s — ignoring start", user_id)
        return

    pf = _PortfolioState(
        user_id=user_id,
        params=params,
        running=True,
        session_maker=session_maker,
    )
    _portfolios[user_id] = pf

    if session_maker is not None:
        await _set_control(
            session_maker,
            user_id,
            True,
            extra={"params": params.model_dump(), "instance_id": pf.instance_id},
        )
        await _persist(pf)

    pf._task = asyncio.create_task(_portfolio_loop(pf), name=f"kimp_paper_pf_{user_id}")
    _log.info(
        "Paper PF started user=%s top_n=%s replica=%s", user_id, params.top_n, _REPLICA_ID
    )


async def stop_portfolio(
    user_id: str,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if session_maker is not None:
        with contextlib.suppress(Exception):
            await _set_control(session_maker, user_id, False)

    pf = _portfolios.get(user_id)
    if pf is None:
        if session_maker is not None:
            with contextlib.suppress(Exception):
                async with session_maker() as session:
                    snap = await get_account_snapshot(session, key=_pf_snapshot_key(user_id))
                    if snap and snap.data_json and snap.data_json.get("running"):
                        data = dict(snap.data_json)
                        data["running"] = False
                        await upsert_account_snapshot(
                            session, key=_pf_snapshot_key(user_id), data_json=data
                        )
                        await session.commit()
        return

    pf.running = False
    if pf._task and not pf._task.done():
        pf._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pf._task

    with contextlib.suppress(Exception):
        await asyncio.shield(_liquidate_slots(pf))
    with contextlib.suppress(Exception):
        await _persist(pf)
    _log.info("Paper PF stopped user=%s replica=%s", user_id, _REPLICA_ID)


async def get_portfolio_status_persisted(
    session: AsyncSession, user_id: str
) -> KimpPaperPortfolioStatus:
    """모든 replica에서 일관된 상태 반환(인메모리 우선, 아니면 스냅샷)."""
    desired = True
    ctl_instance: str | None = None
    ctl = await get_account_snapshot(session, key=_pf_control_key(user_id))
    if ctl and ctl.data_json:
        desired = bool(ctl.data_json.get("desired_running", True))
        ctl_instance = ctl.data_json.get("instance_id")

    pf = _portfolios.get(user_id)
    if (
        pf is not None
        and pf.running
        and desired
        and (ctl_instance is None or ctl_instance == pf.instance_id)
    ):
        return build_status(pf)

    snap = await get_account_snapshot(session, key=_pf_snapshot_key(user_id))
    if not snap or not snap.data_json:
        return KimpPaperPortfolioStatus(running=False)
    data = dict(snap.data_json)
    running = bool(data.get("running")) and desired and not _is_stale(snap.updated_at)
    data["running"] = running
    try:
        return KimpPaperPortfolioStatus(**data)
    except Exception:  # noqa: BLE001
        return KimpPaperPortfolioStatus(running=False)


async def restore_portfolios_on_startup(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """부팅 시 desired_running=true인 페이퍼 포트폴리오를 자동 복원한다(키 불필요)."""
    rows: list[AccountSnapshot] = []
    try:
        async with session_maker() as session:
            result = await session.execute(
                select(AccountSnapshot).where(
                    AccountSnapshot.key.like("kimp_paper_pf_ctl:%")
                )
            )
            rows = list(result.scalars().all())
    except Exception:
        _log.warning("Paper PF auto-restore: DB query failed")
        return

    for snap in rows:
        data = snap.data_json or {}
        if not data.get("desired_running"):
            continue
        user_id = snap.key.split(":", 1)[1]
        if user_id in _portfolios and _portfolios[user_id].running:
            continue
        params_raw = data.get("params")
        if not params_raw:
            continue
        try:
            params = KimpPaperPortfolioParams(**params_raw)
            await start_portfolio(
                user_id=user_id, params=params, session_maker=session_maker
            )
            _log.info("Auto-restored paper PF user=%s top_n=%s", user_id, params.top_n)
        except Exception:
            _log.exception("Paper PF auto-restore failed key=%s", snap.key)


__all__ = [
    "KimpPaperPortfolioParams",
    "build_status",
    "get_portfolio_status_persisted",
    "restore_portfolios_on_startup",
    "start_portfolio",
    "stop_portfolio",
]
