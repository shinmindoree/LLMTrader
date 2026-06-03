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
import os
import socket
import time
import uuid
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
# 상태 스냅샷이 이 시간(초)보다 오래되면 엔진이 죽은 것으로 간주한다.
# 멀티 replica 환경에서 엔진이 소유하지 않은 replica가 좀비 "Running"을
# 보고하지 않도록 하는 heartbeat staleness 임계치. tick 주기(10s)의 배수.
_STATUS_STALE_SECONDS = 60

# 이 프로세스(replica)를 식별하는 ID. Azure Container Apps는 HOSTNAME에
# replica 이름을 넣는다. 소유권(owner) 표기에 사용한다.
_REPLICA_ID = os.environ.get("HOSTNAME") or socket.gethostname()


def _snapshot_key(user_id: str) -> str:
    return f"funding_arb:{user_id}"


def _control_key(user_id: str) -> str:
    """desired-state(원하는 실행 여부) 전용 제어 키.

    관측(observed) 스냅샷과 분리하여, 엔진 주기와 상태 영속화가
    stop 의도를 덮어쓰지 않도록 한다.
    """
    return f"funding_arb_ctl:{user_id}"


def _is_stale(updated: datetime | None) -> bool:
    if updated is None:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (datetime.now(UTC) - updated).total_seconds() > _STATUS_STALE_SECONDS


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
    next_funding_time_ms: int | None = None
    unrealized_pnl: float | None = None
    accumulated_funding_income: float = 0.0
    last_funding_ts: datetime | None = None
    params: FundingArbitrageParams | None = None
    last_error: str | None = None
    futures_dual_side: bool | None = None  # None=미확인, True=헤지모드, False=원웨이모드
    owner: str = _REPLICA_ID  # 이 엔진을 소유한 replica id
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)  # 이 엔진 인스턴스 식별자
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


def _ms_to_dt(ms: int | None) -> datetime | None:
    if not ms or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


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
        next_funding_time=_ms_to_dt(st.next_funding_time_ms),
        unrealized_pnl=st.unrealized_pnl,
        accumulated_funding_income=st.accumulated_funding_income,
        last_funding_ts=st.last_funding_ts,
        params=st.params,
        last_error=st.last_error,
    )


async def _set_desired_running(
    session_maker: async_sessionmaker[AsyncSession],
    user_id: str,
    value: bool,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    """desired-state(원하는 실행 여부)를 DB 제어 키에 기록한다.

    ``extra``로 env/params 등 기동에 필요한 메타데이터를 함께 저장한다(비밀키 제외).
    """
    data: dict[str, Any] = {"desired_running": bool(value)}
    if extra:
        data.update(extra)
    async with session_maker() as session:
        await upsert_account_snapshot(
            session, key=_control_key(user_id), data_json=data
        )
        await session.commit()


async def _get_control_state(
    session_maker: async_sessionmaker[AsyncSession], user_id: str
) -> dict[str, Any]:
    """제어 키(desired_running, instance_id 등)를 반환한다. 없으면 빈 dict."""
    async with session_maker() as session:
        snap = await get_account_snapshot(session, key=_control_key(user_id))
    if not snap or not snap.data_json:
        return {}
    return dict(snap.data_json)


async def get_engine_status_persisted(
    session: AsyncSession, user_id: str
) -> FundingArbitrageStatusResponse:
    """모든 replica에서 일관된 상태를 반환한다.

    이 replica가 엔진을 메모리에 들고 있으면 가장 신선한 인메모리 상태를 우선
    사용하고, 아니면 DB 스냅샷(heartbeat=updated_at)을 읽어 staleness/desired-state를
    반영해 보고한다. 멀티 replica 깜빡임과 좀비 Running을 방지한다.
    """
    desired = True
    ctl_instance: str | None = None
    ctl_symbol: str | None = None
    ctl = await get_account_snapshot(session, key=_control_key(user_id))
    if ctl and ctl.data_json:
        desired = bool(ctl.data_json.get("desired_running", True))
        ctl_instance = ctl.data_json.get("instance_id")
        ctl_symbol = ctl.data_json.get("symbol")

    # 이 replica가 지정 인스턴스를 들고 있으면 가장 신선한 인메모리 상태를 보고.
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
        return FundingArbitrageStatusResponse(running=False, accumulated_funding_income=0.0)

    d = snap.data_json

    # 스냅샷이 지정 인스턴스가 아닌 좀비 엔진이 마지막에 덮어쓴 것이라면(교체 직후
    # 새 엔진이 아직 자기 스냅샷을 쓰기 전), 옛 심볼을 보여주지 않는다. 제어 키의
    # 의도된 심볼로 "기동 중" 상태만 노출해 BTC↔BNB 깜빡임을 방지한다.
    snap_instance = d.get("instance_id")
    if (
        desired
        and ctl_instance is not None
        and snap_instance is not None
        and snap_instance != ctl_instance
    ):
        return FundingArbitrageStatusResponse(
            running=True,
            symbol=ctl_symbol,
            accumulated_funding_income=0.0,
        )

    running = bool(d.get("running")) and desired and not _is_stale(snap.updated_at)

    params = None
    if d.get("params"):
        try:
            params = FundingArbitrageParams(**d["params"])
        except Exception:  # noqa: BLE001
            params = None

    last_dt = None
    last_ts = d.get("last_funding_ts")
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
        except Exception:  # noqa: BLE001
            last_dt = None

    return FundingArbitrageStatusResponse(
        running=running,
        symbol=d.get("symbol"),
        spot_qty=(d.get("spot_qty") or None),
        futures_short_qty=(d.get("futures_short_qty") or None),
        current_funding_rate=d.get("current_funding_rate"),
        annualized_funding_pct=d.get("annualized_funding_pct"),
        next_funding_time=_ms_to_dt(d.get("next_funding_time_ms")),
        unrealized_pnl=d.get("unrealized_pnl"),
        accumulated_funding_income=float(d.get("accumulated_funding_income") or 0.0),
        last_funding_ts=last_dt,
        params=params,
        last_error=d.get("last_error"),
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
        if existing.symbol == params.symbol:
            _log.warning("Engine already running for user=%s — ignoring start", user_id)
            return
        # 같은 replica에 다른 심볼의 잔존(좀비) 엔진이 있으면 먼저 청산·정지하고
        # 교체한다. (사용자가 Stop 후 다른 심볼로 재시작한 경우)
        _log.info(
            "Replacing local engine user=%s: %s -> %s (liquidating old)",
            user_id,
            existing.symbol,
            params.symbol,
        )
        existing.running = False
        if existing._task and not existing._task.done():
            existing._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await existing._task
        with contextlib.suppress(Exception):
            await _liquidate_all(existing)

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
        # desired-state를 true로 기록하고(기동에 필요한 메타 포함), 즉시 초기
        # 스냅샷을 남겨 다른 replica의 상태 조회가 곧바로 Running을 보게 한다.
        # instance_id를 함께 기록하여, 이후 다른 심볼/replica로 새 엔진이 시작되면
        # 기존(좀비) 엔진이 자신이 더 이상 지정 엔진이 아님을 감지해 자가 정지한다.
        env = "testnet" if is_testnet else "mainnet"
        await _set_desired_running(
            session_maker,
            user_id,
            True,
            extra={
                "env": env,
                "is_testnet": is_testnet,
                "params": params.model_dump(),
                "symbol": params.symbol,
                "instance_id": st.instance_id,
            },
        )
        await _persist_state(st)

    st._task = asyncio.create_task(_engine_loop(st), name=f"funding_arb_{user_id}")
    _log.info(
        "Funding arbitrage engine started: user=%s symbol=%s testnet=%s replica=%s",
        user_id,
        params.symbol,
        is_testnet,
        _REPLICA_ID,
    )


async def stop_engine(
    user_id: str,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """엔진을 정지한다.

    desired_running=false를 DB에 기록하여, 엔진이 다른 replica에서 돌더라도
    그 쪽 루프가 다음 tick에 스스로 멈추도록 한다. 로컬 replica가 소유 중이면
    즉시 중지한다.
    """
    if session_maker is not None:
        with contextlib.suppress(Exception):
            await _set_desired_running(session_maker, user_id, False)

    st = _engines.get(user_id)
    if not st or not st.running:
        # 로컬에 없더라도 desired=false는 위에서 기록됨 → 소유 replica가 자가 정지.
        # 마지막으로 관측 스냅샷의 running을 내려 즉시 일관성을 확보한다.
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
    # 거래소 포지션 청산: 현물 SELL + 선물 BUY로 델타 노출 해소
    with contextlib.suppress(Exception):
        await _liquidate_all(st)
    with contextlib.suppress(Exception):
        await _persist_state(st)
    _log.info("Funding arbitrage engine stopped: user=%s replica=%s", user_id, _REPLICA_ID)


# ── base URL 도출 ──────────────────────────────────────────


def _bases_for_env(is_testnet: bool) -> tuple[str, str]:
    """(spot_base, futures_base)를 반환.

    testnet은 Binance Demo Trading 환경을 사용한다. Demo 키 한 쌍이
    선물(testnet.binancefuture.com)과 현물(demo-api.binance.com) 양쪽에서
    모두 인증된다.
    """
    if is_testnet:
        return "https://demo-api.binance.com", "https://testnet.binancefuture.com"
    return "https://api.binance.com", "https://fapi.binance.com"


# ── 영속화 ─────────────────────────────────────────────────


async def _persist_state(st: _EngineState) -> None:
    if st.session_maker is None:
        return

    ann = (
        st.current_funding_rate * _periods_per_year(st.funding_interval_hours) * 100
        if st.current_funding_rate is not None
        else None
    )
    data = {
        "running": st.running,
        "owner": st.owner,
        "instance_id": st.instance_id,
        "symbol": st.symbol,
        "spot_qty": st.spot_qty,
        "futures_short_qty": st.futures_short_qty,
        "entry_mark_price": st.entry_mark_price,
        "entry_ts_ms": st.entry_ts_ms,
        "funding_interval_hours": st.funding_interval_hours,
        "current_funding_rate": st.current_funding_rate,
        "next_funding_time_ms": st.next_funding_time_ms,
        "annualized_funding_pct": ann,
        "unrealized_pnl": st.unrealized_pnl,
        "accumulated_funding_income": st.accumulated_funding_income,
        "last_funding_ts": st.last_funding_ts.isoformat() if st.last_funding_ts else None,
        "last_error": st.last_error,
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
            # 거래소 실제 포지션과 대조해 유령(이미 청산됐거나 한 번도 안 열린)
            # 상태를 정리한다. 그렇지 않으면 has_position=True로 신규 진입이
            # 영구히 스킵된다.
            await _reconcile_restored_position(st)


async def _reconcile_restored_position(st: _EngineState) -> None:
    """복원한 포지션을 거래소 실제 선물 포지션과 대조한다.

    선물 숏이 실제로 존재하지 않으면(positionRisk 수량 ≈ 0) 델타-뉴트럴
    포지션이 더 이상 유효하지 않다고 보고 양 레그를 평탄화(flat)한다.
    이렇게 하면 오래된 스냅샷이 신규 진입을 막는 문제를 방지한다.
    조회 실패 시에는 안전하게 복원값을 유지한다.
    """
    params = st.params
    if params is None:
        return
    try:
        async with httpx.AsyncClient(base_url=st.futures_base, timeout=10.0) as fc:
            resp = await fc.get(
                "/fapi/v2/positionRisk",
                headers=_auth_headers(st.api_key),
                params=_signed_params(st.api_secret, {"symbol": params.symbol}),
            )
            resp.raise_for_status()
            positions: list[dict[str, Any]] = resp.json()
    except Exception:
        _log.warning(
            "Reconcile skipped (user=%s) — positionRisk fetch failed; keeping restored state",
            st.user_id,
        )
        return

    actual_short = 0.0
    for p in positions:
        if p.get("positionSide") in ("SHORT", "BOTH"):
            amt = abs(float(p.get("positionAmt") or 0.0))
            actual_short = max(actual_short, amt)

    if actual_short <= 0.0 and st.futures_short_qty > 0.0:
        _log.info(
            "Reconcile: no live futures short on exchange (user=%s) — clearing stale "
            "position (was spot=%.6f short=%.6f)",
            st.user_id,
            st.spot_qty,
            st.futures_short_qty,
        )
        st.spot_qty = 0.0
        st.futures_short_qty = 0.0
        st.entry_mark_price = 0.0
        st.entry_ts_ms = None
        with contextlib.suppress(Exception):
            await _persist_state(st)


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

            # 매 tick마다 관측 스냅샷을 갱신(heartbeat). 포지션이 없어도 기록하여
            # 모든 replica가 일관된 Running 상태와 신선한 heartbeat를 보게 한다.
            with contextlib.suppress(Exception):
                await _persist_state(st)

            # desired-state 확인: 다른 replica/심볼에서 Stop이나 교체가 일어났다면 자가 정지.
            #  - desired_running=false  → 사용자가 Stop을 눌렀다.
            #  - instance_id 불일치      → 다른 엔진(새 심볼/replica)이 지정 엔진을 넘겨받았다.
            #    이 엔진은 좀비이므로 보유 포지션을 청산하고 종료한다.
            if st.session_maker is not None:
                try:
                    ctl = await _get_control_state(st.session_maker, st.user_id)
                    desired = bool(ctl.get("desired_running", True))
                    ctl_instance = ctl.get("instance_id")
                    superseded = ctl_instance is not None and ctl_instance != st.instance_id
                    if not desired or superseded:
                        _log.info(
                            "Self-stopping (user=%s): desired=%s superseded=%s "
                            "(my_instance=%s ctl_instance=%s)",
                            st.user_id,
                            desired,
                            superseded,
                            st.instance_id,
                            ctl_instance,
                        )
                        st.running = False
                        # 거래소 포지션 청산(현재 루프의 클라이언트 재사용)
                        with contextlib.suppress(Exception):
                            await _unwind_position(
                                st,
                                futures_client=futures_client,
                                spot_client=spot_client,
                            )
                        # 좀비가 교체된 경우 관측 스냅샷을 덮어쓰지 않는다(새 엔진이
                        # 이미 자신의 상태를 기록 중). desired=false로 멈춘 경우에만 기록.
                        if not superseded:
                            with contextlib.suppress(Exception):
                                await _persist_state(st)
                        break
                except Exception:
                    _log.warning("desired-state check failed (user=%s)", st.user_id)

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

    funding_rate, mark_price, next_funding_ms = await _fetch_funding_rate(futures_client, params.symbol)
    st.current_funding_rate = funding_rate
    st.next_funding_time_ms = next_funding_ms
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


async def _fetch_funding_rate(client: httpx.AsyncClient, symbol: str) -> tuple[float, float, int | None]:
    resp = await client.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    rate = float(data.get("lastFundingRate") or 0)
    mark_price = float(data.get("markPrice") or 0)
    next_ft = data.get("nextFundingTime")
    try:
        next_ms = int(next_ft) if next_ft else None
    except (TypeError, ValueError):
        next_ms = None
    if next_ms is not None and next_ms <= 0:
        next_ms = None
    return rate, mark_price, next_ms


async def _fetch_funding_interval(client: httpx.AsyncClient, symbol: str) -> float:
    resp = await client.get("/fapi/v1/fundingInfo")
    resp.raise_for_status()
    rows: list[dict[str, Any]] = resp.json()
    for row in rows:
        if row.get("symbol") == symbol:
            hours = float(row.get("fundingIntervalHours") or _DEFAULT_FUNDING_INTERVAL_HOURS)
            return hours if hours > 0 else _DEFAULT_FUNDING_INTERVAL_HOURS
    return _DEFAULT_FUNDING_INTERVAL_HOURS


async def _resolve_position_side(
    st: _EngineState, futures_client: httpx.AsyncClient
) -> str:
    """선물 계정의 포지션 모드를 감지해 주문에 쓸 positionSide를 반환.

    - 헤지 모드(dualSidePosition=true): 숏 진입/청산에 ``"SHORT"``가 필요.
    - 원웨이 모드(기본값, dualSidePosition=false): ``"BOTH"``를 사용해야 하며
      ``"SHORT"``를 보내면 -4061 오류가 난다. (Binance 데모/testnet 기본값은 원웨이)

    결과를 ``st.futures_dual_side``에 캐시하여 매 주문마다 조회하지 않는다.
    조회 실패 시 안전하게 원웨이('BOTH')로 가정한다.
    """
    if st.futures_dual_side is None:
        try:
            params = _signed_params(st.api_secret, {})
            resp = await futures_client.get(
                "/fapi/v1/positionSide/dual",
                headers=_auth_headers(st.api_key),
                params=params,
            )
            resp.raise_for_status()
            st.futures_dual_side = bool(resp.json().get("dualSidePosition"))
            _log.info(
                "Futures position mode for user=%s: %s",
                st.user_id,
                "HEDGE" if st.futures_dual_side else "ONE-WAY",
            )
        except Exception:
            _log.warning(
                "positionSide/dual fetch failed (user=%s) — assuming ONE-WAY", st.user_id
            )
            st.futures_dual_side = False
    return "SHORT" if st.futures_dual_side else "BOTH"


# ── 심볼 필터 (LOT_SIZE / minNotional) ─────────────────────


def _round_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    return math.floor(qty / step) * step


def _resolved_filled_qty(order: dict[str, Any], fallback: float) -> float:
    """주문 응답의 체결 수량을 안전하게 해석한다.

    Binance가 ``executedQty``를 문자열 ``"0"``(예: 선물 ACK 응답)로 주면
    ``float(... or fallback)`` 패턴이 ``"0"``을 truthy로 보아 0이 되는 버그가
    있었다. 명시적으로 0 초과 여부를 확인하고, 아니면 ``fallback``을 쓴다.
    """
    raw = order.get("executedQty")
    try:
        qty = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        qty = 0.0
    return qty if qty > 0 else fallback


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

    # 현물 시장이 존재하는지(특히 testnet) 확인 — 없으면 명확한 사유를 노출하고 중단.
    try:
        spot_flt = await _get_filter(spot_client, st.spot_base, params.symbol, is_futures=False)
    except Exception as exc:
        msg = (
            f"{params.symbol} 현물 시장을 찾을 수 없습니다 "
            f"({'testnet(데모)' if st.is_testnet else 'mainnet'}). "
            "이 전략은 현물 매수 + 선물 숏 구조라 현물 시장이 필수입니다. "
            "현물·선물 모두 상장된 심볼(예: BTCUSDT, BNBUSDT)을 선택하세요."
        )
        if st.last_error != msg:
            _log.warning("Entry blocked (user=%s): %s [%s]", st.user_id, msg, exc)
        st.last_error = msg
        return
    fut_flt = await _get_filter(futures_client, st.futures_base, params.symbol, is_futures=True)

    # 현물·선물 수량을 동일하게 유지하기 위해 더 거친 step/min을 사용
    step = max(spot_flt.step_size, fut_flt.step_size)
    min_qty = max(spot_flt.min_qty, fut_flt.min_qty)
    min_notional = max(spot_flt.min_notional, fut_flt.min_notional)

    raw_qty = params.allocated_usdt / mark_price
    qty = _round_step(raw_qty, step)
    if qty < min_qty or qty <= 0:
        msg = (
            f"진입 수량 {qty:.8f}이 최소 주문 수량 {min_qty:.8f} 미만입니다. "
            "할당 시드(USDT)를 늘리세요."
        )
        st.last_error = msg
        _log.warning("Entry skipped (user=%s): %s", st.user_id, msg)
        return
    if min_notional and qty * mark_price < min_notional:
        msg = (
            f"진입 명목가치 {qty * mark_price:.2f} USDT가 최소 명목가치 {min_notional:.2f} 미만입니다. "
            "할당 시드(USDT)를 늘리세요."
        )
        st.last_error = msg
        _log.warning("Entry skipped (user=%s): %s", st.user_id, msg)
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
        filled = _resolved_filled_qty(spot_order, qty)
        st.spot_qty = filled
        st.last_error = None
        _log.info("Spot BUY filled (user=%s): qty=%s", st.user_id, qty_str)
    except Exception as exc:
        st.last_error = f"현물 매수 주문 실패: {exc}. 현물 지갑 USDT 잔고를 확인하세요."
        _log.exception("Spot BUY failed (user=%s) — aborting entry", st.user_id)
        return

    # 2) 선물 시장가 숏 — 현물 체결 수량을 step에 맞춰 재정렬
    fut_qty = _round_step(st.spot_qty, fut_flt.step_size or step)
    fut_qty_str = _fmt_qty(fut_qty, fut_flt.step_size or step)
    position_side = await _resolve_position_side(st, futures_client)
    try:
        fut_order = await _place_futures_order(
            client=futures_client,
            api_key=st.api_key,
            api_secret=st.api_secret,
            symbol=params.symbol,
            side="SELL",
            qty_str=fut_qty_str,
            position_side=position_side,
        )
        st.futures_short_qty = _resolved_filled_qty(fut_order, fut_qty)
        st.entry_mark_price = mark_price
        st.entry_ts_ms = int(time.time() * 1000)
        st.last_error = None
        _log.info("Futures SHORT filled (user=%s): qty=%s", st.user_id, fut_qty_str)
        await _persist_state(st)
    except Exception as exc:
        st.last_error = (
            f"선물 숏 주문 실패: {exc}. 선물 지갑 마진(USDT) 잔고와 포지션 모드를 확인하세요. "
            "현물 레그는 롤백했습니다."
        )
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
    position_side = await _resolve_position_side(st, futures_client)

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
                    position_side=position_side,
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


async def _sync_qty_from_exchange(
    st: _EngineState,
    *,
    futures_client: httpx.AsyncClient,
    spot_client: httpx.AsyncClient,
) -> None:
    """청산 직전, 거래소 실제 보유분으로 메모리 수량을 보정한다.

    진입 tick이 끝나기 전(주문은 체결됐지만 ``st.spot_qty``/``futures_short_qty``가
    아직 0)에 STOP되면 task가 ``await`` 지점에서 취소되어 수량이 메모리에 반영되지
    않는다. 그 상태로 ``_liquidate_all``을 호출하면 (0,0)으로 보고 청산을 건너뛰어
    거래소에 고아 포지션이 남는다. 이를 막기 위해 선물 positionRisk·현물 account를
    조회해 거래소 실제 보유분을 메모리값과 ``max``로 합산한다. 조회 실패 시 안전하게
    메모리값을 유지한다.

    현물 base asset 잔고 전량을 청산 대상으로 본다 — 이 전략은 전용 계정에서
    동작한다고 가정한다(전략이 직접 매수한 현물 롱 레그를 닫는다).
    """
    params = st.params
    if params is None:
        return

    # 선물 실제 숏 포지션
    with contextlib.suppress(Exception):
        resp = await futures_client.get(
            "/fapi/v2/positionRisk",
            headers=_auth_headers(st.api_key),
            params=_signed_params(st.api_secret, {"symbol": params.symbol}),
        )
        resp.raise_for_status()
        for p in resp.json():
            if p.get("positionSide") in ("SHORT", "BOTH"):
                amt = abs(float(p.get("positionAmt") or 0.0))
                if amt > st.futures_short_qty:
                    st.futures_short_qty = amt

    # 현물 실제 base asset 보유분
    with contextlib.suppress(Exception):
        base_asset = (
            params.symbol[:-4] if params.symbol.endswith("USDT") else params.symbol
        )
        resp = await spot_client.get(
            "/api/v3/account",
            headers=_auth_headers(st.spot_api_key),
            params=_signed_params(st.spot_api_secret, {}),
        )
        resp.raise_for_status()
        balances: list[dict[str, Any]] = resp.json().get("balances", [])
        free_base = next(
            (float(b["free"]) for b in balances if b.get("asset") == base_asset),
            0.0,
        )
        if free_base > st.spot_qty:
            spot_flt = await _get_filter(
                spot_client, st.spot_base, params.symbol, is_futures=False
            )
            # step/min 미만 dust는 청산 대상에서 제외 (주문 거부 방지)
            if free_base >= max(spot_flt.min_qty, spot_flt.step_size):
                st.spot_qty = _round_step(free_base, spot_flt.step_size)


async def _liquidate_all(st: _EngineState) -> None:
    """보유 중인 현물·선물 레그를 전량 청산해 거래소 포지션을 비운다.

    STOP 또는 다른 replica의 desired=false 자가정지 시 호출한다. 자체 httpx
    클라이언트를 열어 ``_unwind_position``으로 분할 청산한다. 베스트-에포트로,
    실패해도 정지 흐름을 막지 않는다.

    먼저 ``_sync_qty_from_exchange``로 거래소 실제 보유분을 조회·보정한다.
    이렇게 하면 진입 도중 STOP되어 메모리 수량이 0이어도 거래소에 체결된
    포지션을 정확히 청산한다.
    """
    try:
        async with (
            httpx.AsyncClient(base_url=st.futures_base, timeout=10.0) as futures_client,
            httpx.AsyncClient(base_url=st.spot_base, timeout=10.0) as spot_client,
        ):
            await _sync_qty_from_exchange(
                st, futures_client=futures_client, spot_client=spot_client
            )
            if st.spot_qty <= 0 and st.futures_short_qty <= 0:
                _log.info(
                    "Liquidate on stop: nothing to close (user=%s)", st.user_id
                )
                return
            _log.info(
                "Liquidating on stop (user=%s): spot=%.6f short=%.6f",
                st.user_id,
                st.spot_qty,
                st.futures_short_qty,
            )
            await _unwind_position(
                st, futures_client=futures_client, spot_client=spot_client
            )
    except Exception:
        _log.exception(
            "Liquidation on stop failed (user=%s) — positions may remain open", st.user_id
        )


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
        {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty_str,
            "newOrderRespType": "RESULT",
        },
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
            "newOrderRespType": "RESULT",
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
