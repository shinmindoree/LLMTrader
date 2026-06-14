from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import logging
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.strategy_storage import get_strategy_storage
from control.alembic_upgrade import run_alembic_upgrade_head
from control.db import create_async_engine, create_session_maker, init_db
from control.enums import EventKind, JobStatus, JobType
from control.models import WalletAccountStatus, WalletRole
from control.repo import (
    append_event,
    count_jobs,
    create_job,
    create_strategy_quality_log,
    delete_job,
    delete_jobs,
    delete_strategy_meta_by_name,
    get_account_snapshot,
    get_job,
    get_strategy_meta_by_name,
    get_wallet_account,
    list_events,
    list_job_summaries,
    list_jobs,
    list_orders,
    list_sweep_child_rows,
    list_sweep_group_rows,
    list_strategy_meta,
    list_strategy_quality_logs,
    list_trades,
    list_trades_batch,
    request_stop,
    stop_all_jobs,
    upsert_order,
    upsert_strategy_meta,
)
from control.repo import (
    delete_strategy_chat_session as repo_delete_strategy_chat_session,
)
from control.repo import (
    get_strategy_chat_session as repo_get_strategy_chat_session,
)
from control.repo import (
    list_strategy_chat_session_summaries as repo_list_strategy_chat_session_summaries,
)
from control.repo import (
    list_strategy_chat_sessions as repo_list_strategy_chat_sessions,
)
from control.repo import (
    upsert_strategy_chat_session as repo_upsert_strategy_chat_session,
)
from llm.client import LLMClient
from settings import get_settings

try:
    from llm.capability_registry import (
        SUPPORTED_CONTEXT_METHODS as LOCAL_SUPPORTED_CONTEXT_METHODS,
    )
    from llm.capability_registry import (
        SUPPORTED_DATA_SOURCES as LOCAL_SUPPORTED_DATA_SOURCES,
    )
    from llm.capability_registry import (
        SUPPORTED_INDICATOR_SCOPES as LOCAL_SUPPORTED_INDICATOR_SCOPES,
    )
    from llm.capability_registry import (
        UNSUPPORTED_CAPABILITY_RULES as LOCAL_UNSUPPORTED_CAPABILITY_RULES,
    )
    from llm.capability_registry import (
        capability_summary_lines as local_capability_summary_lines,
    )
except Exception:  # pragma: no cover - fallback for minimal runtime packaging
    LOCAL_SUPPORTED_DATA_SOURCES = ()
    LOCAL_SUPPORTED_INDICATOR_SCOPES = ()
    LOCAL_SUPPORTED_CONTEXT_METHODS = ()
    LOCAL_UNSUPPORTED_CAPABILITY_RULES = ()

    def local_capability_summary_lines() -> list[str]:
        return []


from api.deps import AuthenticatedUser, require_admin, require_auth, set_session_maker
from api.job_policy import evaluate_job_policy
from api.schemas import (
    AdminUserItem,
    AdminUsersResponse,
    AllocationSlice,
    AutoSweepSettingsRequest,
    AutoSweepStatusResponse,
    BinanceAccountSummaryResponse,
    BinanceAssetBalance,
    BinanceCredentialStatus,
    BinancePositionSummary,
    CountItem,
    DeleteAllResponse,
    DeleteResponse,
    FundingArbitrageParams,
    FundingArbitrageStatusResponse,
    FundingExtremePoint,
    FundingScreenerItem,
    FundingScreenerResponse,
    FundingSymbolDetailPoint,
    FundingSymbolDetailResponse,
    FundingWindowStat,
    HealthResponse,
    JobCountsResponse,
    JobCreateRequest,
    JobEventResponse,
    JobPolicyCheckRequest,
    JobPolicyCheckResponse,
    JobResponse,
    JobSummary,
    KimpArbitrageParams,
    KimpArbitrageStatusResponse,
    KimpBacktestEquityPoint,
    KimpBacktestMetrics,
    KimpBacktestRequest,
    KimpBacktestResponse,
    KimpFundingPoint,
    KimpFxRateResponse,
    KimpHistoryPoint,
    KimpHistoryResponse,
    KimpScreenerItem,
    KimpScreenerResponse,
    LivePositionsResponse,
    LivePositionsTotals,
    LiveStrategyPositions,
    LlmTestRequest,
    LlmTestResponse,
    ManualLiveOrderRequest,
    ManualLiveOrderResponse,
    ManualLiveOrderSizingResponse,
    OrderResponse,
    PortfolioSummaryResponse,
    QuickBacktestRequest,
    QuickBacktestResponse,
    StopAllResponse,
    StopResponse,
    SweepCreateRequest,
    SweepCreateResponse,
    SweepDetailResponse,
    SweepDimensionResolved,
    SweepListItem,
    SweepPreflightRequest,
    SweepPreflightResponse,
    SweepRunPreview,
    SweepRunResult,
    StrategyCapabilityResponse,
    StrategyChatRequest,
    StrategyChatResponse,
    StrategyChatSessionResponse,
    StrategyChatSessionSummary,
    StrategyChatSessionUpsertRequest,
    StrategyContentResponse,
    StrategyGenerateRequest,
    StrategyGenerateResponse,
    StrategyInfo,
    StrategyIntakeRequest,
    StrategyIntakeResponse,
    StrategyModuleCatalogResponse,
    StrategyModuleStatus,
    StrategyParamsApplyRequest,
    StrategyParamsApplyResponse,
    StrategyParamsExtractRequest,
    StrategyParamsExtractResponse,
    StrategyQualitySummaryResponse,
    StrategySaveRequest,
    StrategySaveResponse,
    StrategySyntaxCheckRequest,
    StrategySyntaxCheckResponse,
    StrategySyntaxError,
    TradeResponse,
    WalletBalance,
    WalletOverviewResponse,
    WalletSnapshot,
)
from api.strategy_catalog import list_strategy_files, validate_strategy_path
from api.strategy_params import (
    StrategyParamsError,
    apply_strategy_params,
    extract_strategy_params,
)

INTERNAL_JOB_CONFIG_KEYS = {"_strategy_code"}

_log = logging.getLogger("api")

# ── Funding-arb helpers ─────────────────────────────────────────────────────

# VIP0 conservative roundtrip: spot taker 0.10% + futures taker 2×0.05% = 0.20%
_FUNDING_ROUNDTRIP_COST = 0.0020
# hold_days → exit threshold ratio (fraction of entry to exit at)
_FUNDING_EXIT_RATIOS: dict[int, float] = {1: 0.50, 3: 0.25}


def _make_funding_redis() -> Any | None:
    """Synchronous Redis client for reading funding stats (uses API server env vars)."""
    import os

    from common.redis_client import (
        create_redis_client,
        create_redis_client_from_parts,
        create_redis_client_with_aad,
    )

    host = os.environ.get("REDIS_HOST", "")
    username = os.environ.get("REDIS_USERNAME", "")
    password = os.environ.get("REDIS_PASSWORD", "")
    url = os.environ.get("REDIS_URL", "")
    port = int(os.environ.get("REDIS_PORT", "6380"))
    ssl_flag = os.environ.get("REDIS_SSL", "true").strip().lower() != "false"

    if host and username:
        return create_redis_client_with_aad(host=host, username=username, port=port, ssl=ssl_flag)
    if host and password:
        return create_redis_client_from_parts(host=host, port=port, password=password, ssl=ssl_flag)
    if url:
        return create_redis_client(url)
    return None


def _resolve_funding_deadband(symbol: str, hold_days: int) -> tuple[float, float] | None:
    """Compute (entry_pct, exit_pct) per settlement from Redis AR(1)/OU stats.

    Returns None when stats are unavailable or half-life is invalid.
    """
    import json as _json

    rd = _make_funding_redis()
    if rd is None:
        return None
    try:
        raw = rd.get(f"funding:stats:{symbol}")
        if not raw:
            return None
        stat = _json.loads(raw)
        hl = float(stat.get("half_life_settlements") or 0)
        if hl <= 0:
            return None
        entry_pct = _FUNDING_ROUNDTRIP_COST / hl
        exit_ratio = _FUNDING_EXIT_RATIOS.get(hold_days, 0.30)
        return entry_pct, entry_pct * exit_ratio
    except Exception:
        _log.warning(
            "Failed to resolve deadband for %s hold_days=%s", symbol, hold_days, exc_info=True
        )
        return None


# 펀딩 차익거래는 현물 롱 + 선물 숏 구조이므로 후보 심볼은 반드시 현물 시장에도 상장돼야 한다.
# Binance 데모(testnet) 현물은 mainnet 현물 상장 목록을 그대로 미러링하므로, mainnet 현물
# universe를 두 환경 공통 필터로 사용한다. exchangeInfo 페이로드가 크므로 1시간 캐시한다.
_SPOT_SYMBOLS_CACHE: dict[str, Any] = {}
_SPOT_SYMBOLS_TTL = 3600.0


async def _fetch_tradable_spot_symbols(testnet: bool = False) -> set[str]:
    """현재 거래(TRADING) 가능한 현물 심볼 집합을 반환(1시간 캐시).

    조회 실패 시 마지막으로 캐시된 집합(없으면 빈 집합)을 반환하여, 스크리너가
    필터 때문에 전부 비는 일이 없도록 한다(빈 집합이면 호출 측에서 필터를 건너뜀).

    testnet=True이면 데모 트레이딩 현물(demo-api.binance.com)을 조회한다.
    운영망과 캐시를 분리하여 환경별 상장 차이를 정확히 반영한다.
    """
    import time as _time

    now = _time.time()
    cache_key = "testnet" if testnet else "mainnet"
    cached = _SPOT_SYMBOLS_CACHE.setdefault(cache_key, {"symbols": set(), "ts": 0.0})
    if cached["symbols"] and (now - cached["ts"]) < _SPOT_SYMBOLS_TTL:
        return cached["symbols"]
    base_url = "https://demo-api.binance.com" if testnet else "https://api.binance.com"
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            resp = await client.get("/api/v3/exchangeInfo")
            resp.raise_for_status()
            syms = {
                s["symbol"]
                for s in resp.json().get("symbols", [])
                if isinstance(s, dict) and s.get("status") == "TRADING"
            }
        if syms:
            cached["symbols"] = syms
            cached["ts"] = now
        return syms
    except Exception:
        _log.warning("Failed to fetch spot symbols for screener filter", exc_info=True)
        return cached["symbols"]


# 24h 현물 거래대금(quoteVolume) 캐시: /api/v3/ticker/24hr 한 번에 전체 마켓 반환.
# 페이로드가 크고 분 단위로만 갱신되어도 충분하므로 5분 캐시.
_SPOT_24H_CACHE: dict[str, Any] = {}
_SPOT_24H_TTL = 300.0


async def _fetch_spot_24h_quote_volume(testnet: bool = False) -> dict[str, float]:
    """심볼별 24시간 현물 거래대금(USDT) 매핑을 반환(5분 캐시).

    Binance는 운영 mainnet의 24h 통계만 의미가 있다. testnet은 거래량이 없고
    데모 환경의 ticker는 시가총액·거래량 의사결정에 부적합하므로 두 환경
    모두 mainnet(api.binance.com)을 조회한다. 캐시 키는 단일.
    """
    import time as _time

    now = _time.time()
    cached = _SPOT_24H_CACHE.setdefault("mainnet", {"data": {}, "ts": 0.0})
    if cached["data"] and (now - cached["ts"]) < _SPOT_24H_TTL:
        return cached["data"]
    try:
        async with httpx.AsyncClient(base_url="https://api.binance.com", timeout=10.0) as client:
            resp = await client.get("/api/v3/ticker/24hr")
            resp.raise_for_status()
            out: dict[str, float] = {}
            for row in resp.json():
                if not isinstance(row, dict):
                    continue
                sym = row.get("symbol")
                if not isinstance(sym, str):
                    continue
                try:
                    out[sym] = float(row.get("quoteVolume") or 0.0)
                except (TypeError, ValueError):
                    continue
        if out:
            cached["data"] = out
            cached["ts"] = now
        return out
    except Exception:
        _log.warning("Failed to fetch 24h quote volume", exc_info=True)
        return cached["data"]


# 시가총액(CoinGecko) 캐시. /coins/markets는 페이지당 250개·USD 시가총액 반환.
# 무료 API rate limit(분당 ~30회)를 고려해 1시간 캐시. 4페이지(=1000개)면 Binance
# 상장 종목 대부분을 커버한다. 외부 의존이라 실패해도 None으로 우아하게 누락한다.
_MARKET_CAP_CACHE: dict[str, Any] = {"data": {}, "ts": 0.0}
_MARKET_CAP_TTL = 3600.0
_COINGECKO_PAGES = 4


async def _fetch_market_caps() -> dict[str, float]:
    """심볼(대문자, 예: 'BTC')→USD 시가총액 매핑을 반환(1시간 캐시).

    CoinGecko /coins/markets에서 시가총액 내림차순으로 상위 ``_COINGECKO_PAGES * 250``
    종목을 받아 ``symbol`` 필드(소문자)를 대문자로 정규화해 매핑한다. 심볼이 중복되는
    경우(예: 'BNB'가 여러 코인) 시가총액이 큰 첫 항목이 유지된다(API가 이미 desc 정렬).
    외부 호출이 실패하면 마지막 캐시(없으면 빈 dict)를 반환한다.
    """
    import time as _time

    now = _time.time()
    if _MARKET_CAP_CACHE["data"] and (now - _MARKET_CAP_CACHE["ts"]) < _MARKET_CAP_TTL:
        return _MARKET_CAP_CACHE["data"]
    out: dict[str, float] = {}
    try:
        async with httpx.AsyncClient(base_url="https://api.coingecko.com", timeout=15.0) as client:
            for page in range(1, _COINGECKO_PAGES + 1):
                resp = await client.get(
                    "/api/v3/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": 250,
                        "page": page,
                        "sparkline": "false",
                    },
                )
                if resp.status_code != 200:
                    _log.warning("CoinGecko markets page=%d status=%d", page, resp.status_code)
                    break
                rows = resp.json()
                if not isinstance(rows, list) or not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    sym = row.get("symbol")
                    mcap = row.get("market_cap")
                    if not isinstance(sym, str) or mcap is None:
                        continue
                    key = sym.upper()
                    if key in out:
                        continue
                    try:
                        out[key] = float(mcap)
                    except (TypeError, ValueError):
                        continue
        if out:
            _MARKET_CAP_CACHE["data"] = out
            _MARKET_CAP_CACHE["ts"] = now
        return out
    except Exception:
        _log.warning("Failed to fetch market caps from CoinGecko", exc_info=True)
        return _MARKET_CAP_CACHE["data"]


# 종목별 펀딩비 상세 응답 캐시. 페이로드는 운영망 fapi를 두 번 호출(1095행)해서
# 만들기 때문에 1시간 캐시한다(펀딩 주기 8h이므로 충분).
_SYMBOL_DETAIL_CACHE: dict[str, tuple[float, Any]] = {}
_SYMBOL_DETAIL_TTL = 3600.0
_SYMBOL_DETAIL_MAX_POINTS = 500  # 차트 다운샘플 상한(전체 기간 ≈ 5~7년)


async def _funding_symbol_detail_cached(symbol: str) -> Any:
    """``funding_arb_symbol_detail`` 핸들러용 캐시 래퍼.

    심볼별로 응답 객체를 1시간 캐시. 동시 요청이 들어와도 멱등하게 동작한다.
    """
    import time as _time

    from api.schemas import (
        FundingSymbolDetailResponse,
        FundingWindowStat,
    )

    now = _time.time()
    cached = _SYMBOL_DETAIL_CACHE.get(symbol)
    if cached and (now - cached[0]) < _SYMBOL_DETAIL_TTL:
        return cached[1]

    rows = await _fetch_funding_history(symbol, days=None)  # 전체 기간
    as_of = datetime.now(UTC)

    if not rows:
        resp = FundingSymbolDetailResponse(
            symbol=symbol,
            as_of=as_of,
            n_samples=0,
            window_stats=[],
            max=None,
            min=None,
            series=[],
            error="펀딩비 이력을 가져오지 못했습니다.",
        )
        # 실패 응답은 짧게 캐시(60초)하여 외부 장애 시 폭주 방지.
        _SYMBOL_DETAIL_CACHE[symbol] = (now - (_SYMBOL_DETAIL_TTL - 60), resp)
        return resp

    DEFAULT_INTERVAL_H = 8.0
    PPY = (365 * 24) / DEFAULT_INTERVAL_H

    now_ms = int(as_of.timestamp() * 1000)
    WINDOWS = (
        ("1w", 7),
        ("1m", 30),
        ("6m", 180),
        ("1y", 365),
        ("all", None),  # 받은 데이터 전체
    )

    window_stats: list[FundingWindowStat] = []
    for label, days in WINDOWS:
        if days is None:
            window = rows
        else:
            cutoff = now_ms - days * 24 * 3600 * 1000
            window = [r for r in rows if r[0] >= cutoff]
        n = len(window)
        if n == 0:
            window_stats.append(
                FundingWindowStat(label=label, avg_pct=None, annualized_pct=None, n_samples=0)
            )
            continue
        avg = sum(r[1] for r in window) / n  # 소수 단위 (예: 0.0001 = 0.01%)
        avg_pct = round(avg * 100.0, 5)
        ann = round(avg * PPY * 100.0, 2)
        window_stats.append(
            FundingWindowStat(label=label, avg_pct=avg_pct, annualized_pct=ann, n_samples=n)
        )

    # 최대/최소: 전체 기간(계약 상장 이후 전체) 기준.
    max_row = max(rows, key=lambda r: r[1])
    min_row = min(rows, key=lambda r: r[1])
    max_pt = FundingExtremePoint(
        rate_pct=round(max_row[1] * 100.0, 5),
        ts=datetime.fromtimestamp(max_row[0] / 1000.0, tz=UTC),
    )
    min_pt = FundingExtremePoint(
        rate_pct=round(min_row[1] * 100.0, 5),
        ts=datetime.fromtimestamp(min_row[0] / 1000.0, tz=UTC),
    )

    # 차트 시계열: 전체 기간을 균등 간격으로 다운샘플하여 ≤ _SYMBOL_DETAIL_MAX_POINTS 포인트.
    series_raw = rows
    if len(series_raw) > _SYMBOL_DETAIL_MAX_POINTS:
        step = len(series_raw) / _SYMBOL_DETAIL_MAX_POINTS
        sampled_idx = {int(i * step) for i in range(_SYMBOL_DETAIL_MAX_POINTS)}
        # 마지막 포인트는 반드시 포함.
        sampled_idx.add(len(series_raw) - 1)
        series_pts = [series_raw[i] for i in sorted(sampled_idx)]
    else:
        series_pts = series_raw
    series = [FundingSymbolDetailPoint(t=int(t), r=round(r * 100.0, 5)) for t, r in series_pts]

    resp = FundingSymbolDetailResponse(
        symbol=symbol,
        as_of=as_of,
        n_samples=len(rows),
        window_stats=window_stats,
        max=max_pt,
        min=min_pt,
        series=series,
        error=None,
    )
    _SYMBOL_DETAIL_CACHE[symbol] = (now, resp)
    return resp


# 김프 히스토리 차트 range별 펀딩 이력 조회 일수(캔들 윈도우보다 약간 넓게 잡아
# 윈도우 좌측 끝까지 펀딩 라인이 그려지도록 한다).
_FUNDING_DAYS_BY_RANGE: dict[str, int] = {
    "1H": 1,
    "1D": 2,
    "7D": 8,
    "30D": 31,
    "ALL": 366,
}


async def _fetch_funding_history(
    symbol: str, *, days: int | None = None
) -> list[tuple[int, float]]:
    """운영망 fapi.binance.com에서 ``symbol``의 펀딩비 이력을 가져온다.

    ``days``가 None이면 계약 상장 이후 **전체 기간**을 수집한다(예: BTCUSDT
    ≈ 7,400행, 약 5~6년). ``days``가 정수면 최근 ``days``일만 가져온다.

    반환: ``[(funding_time_ms, funding_rate_as_decimal), ...]`` (오름차순).
    실패 시 빈 리스트.

    페이지네이션: 한 번에 최대 1000행 → 전체는 ~10페이지 이내.
    """
    PATH = "/fapi/v1/fundingRate"
    LIMIT = 1000
    end_ms = int(datetime.now().timestamp() * 1000)
    if days is None:
        start_ms = 0  # 계약 상장 이후 전체 (Binance가 자동으로 첫 페이지부터 반환)
    else:
        start_ms = max(0, end_ms - days * 24 * 3600 * 1000)

    out: list[tuple[int, float]] = []
    cursor = start_ms
    try:
        async with httpx.AsyncClient(base_url="https://fapi.binance.com", timeout=20.0) as client:
            # 안전상 최대 30페이지(=30,000행 ≈ 27년) — 어떤 영구계약도 충분히 커버.
            for _ in range(30):
                resp = await client.get(
                    PATH,
                    params={
                        "symbol": symbol,
                        "limit": LIMIT,
                        "startTime": cursor,
                        "endTime": end_ms,
                    },
                )
                if resp.status_code != 200:
                    _log.warning(
                        "fundingRate fetch failed symbol=%s status=%d body=%s",
                        symbol,
                        resp.status_code,
                        resp.text[:200],
                    )
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
                    out.append((ts, rate))
                    last_ts = max(last_ts, ts)
                if len(rows) < LIMIT:
                    break
                # 다음 페이지: 마지막 ts + 1ms 부터
                cursor = last_ts + 1
                if cursor >= end_ms:
                    break
    except Exception:  # noqa: BLE001
        _log.warning("fundingRate fetch error symbol=%s", symbol, exc_info=True)
        return out

    # 중복 제거 + 시간순 정렬
    seen: set[int] = set()
    dedup: list[tuple[int, float]] = []
    for ts, r in sorted(out, key=lambda x: x[0]):
        if ts in seen:
            continue
        seen.add(ts)
        dedup.append((ts, r))
    return dedup


def _public_job_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    return {k: v for k, v in config.items() if k not in INTERNAL_JOB_CONFIG_KEYS}


def _logical_strategy_path(strategy_name: str) -> str:
    return f"scripts/strategies/{strategy_name}"


def _strategy_name_from_path(path: str) -> str:
    name = Path((path or "").strip()).name
    if not name.endswith(".py"):
        raise ValueError("strategy_path must point to a .py file")
    return name


def _job_to_response(job: Any) -> JobResponse:
    return JobResponse(
        job_id=job.job_id,
        type=JobType(str(job.type)),
        status=job.status,
        strategy_path=job.strategy_path,
        wallet_account_id=getattr(job, "wallet_account_id", None),
        config=_public_job_config(job.config_json),
        result=job.result_json,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        ended_at=job.ended_at,
    )


def _job_summary_row_to_response(row: Any) -> JobSummary:
    """Build a ``JobSummary`` from a row produced by ``list_job_summaries``.

    The repo layer already strips heavy keys (``chart``, ``trades``) from
    ``result_json`` via SQL projection, so we never load multi-MB JSONB blobs
    into the API process. ``row.result_summary`` is therefore safe to pass
    through directly.
    """
    raw_summary = row.result_summary
    summary: dict[str, Any] | None
    if isinstance(raw_summary, dict):
        summary = raw_summary
    else:
        summary = None
    return JobSummary(
        job_id=row.job_id,
        type=JobType(str(row.type)),
        status=row.status,
        strategy_path=row.strategy_path,
        wallet_account_id=row.wallet_account_id,
        config=_public_job_config(row.config_json),
        result_summary=summary,
        error=row.error,
        created_at=row.created_at,
        started_at=row.started_at,
        ended_at=row.ended_at,
    )


def _event_to_response(ev: Any) -> JobEventResponse:
    return JobEventResponse(
        event_id=int(ev.event_id),
        job_id=ev.job_id,
        ts=ev.ts,
        kind=EventKind(str(ev.kind)),
        level=ev.level,
        message=ev.message,
        payload=ev.payload_json,
    )


def _job_env(config: dict[str, Any] | None) -> str:
    env = str((config or {}).get("env") or "mainnet").strip().lower()
    return "testnet" if env == "testnet" else "mainnet"


def _job_symbols(config: dict[str, Any] | None) -> list[str]:
    cfg = config or {}
    symbols: list[str] = []
    streams = cfg.get("streams")
    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            symbol = str(stream.get("symbol") or "").strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    symbol = str(cfg.get("symbol") or "").strip().upper()
    if symbol and symbol not in symbols:
        symbols.append(symbol)
    return symbols


def _position_amt(position_payload: Any, symbol: str) -> float:
    rows = position_payload if isinstance(position_payload, list) else [position_payload]
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("symbol") or "").strip().upper()
        if row_symbol and row_symbol != symbol:
            continue
        try:
            return float(row.get("positionAmt") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_float(value: Any, default: float) -> float:
    parsed = _float_or_none(value)
    if parsed is None or parsed <= 0:
        return default
    return parsed


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _manual_symbol_config(
    config: dict[str, Any] | None,
    symbol: str,
) -> dict[str, Any]:
    cfg = config or {}
    streams = cfg.get("streams")
    if isinstance(streams, list):
        for raw in streams:
            if not isinstance(raw, dict):
                continue
            stream_symbol = str(raw.get("symbol") or "").strip().upper()
            if stream_symbol == symbol:
                return raw
    if str(cfg.get("symbol") or "").strip().upper() == symbol:
        return cfg
    return {}


def _manual_position_row(account: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    raw_positions = account.get("positions", [])
    if not isinstance(raw_positions, list):
        return None
    for raw in raw_positions:
        if not isinstance(raw, dict):
            continue
        row_symbol = str(raw.get("symbol") or "").strip().upper()
        if row_symbol == symbol:
            return raw
    return None


def _manual_exchange_filters(
    exchange_info: dict[str, Any] | None,
    symbol: str,
) -> dict[str, Any]:
    if not isinstance(exchange_info, dict):
        return {}
    raw = exchange_info.get(symbol)
    if isinstance(raw, dict):
        return raw
    return exchange_info


def _quantity_from_notional(
    notional_usdt: float,
    mark_price: float,
    filters: dict[str, Any],
) -> float:
    if notional_usdt <= 0 or mark_price <= 0:
        return 0.0
    qty = Decimal(str(notional_usdt)) / Decimal(str(mark_price))
    step = _decimal_or_none(filters.get("step_size"))
    if step is not None:
        qty = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
    max_qty = _decimal_or_none(filters.get("max_qty"))
    if max_qty is not None and qty > max_qty:
        qty = max_qty
    return float(max(Decimal("0"), qty))


def _manual_entry_sizing_from_state(
    *,
    config: dict[str, Any] | None,
    account: dict[str, Any],
    exchange_filters: dict[str, Any] | None,
    symbol: str,
    side: Literal["LONG", "SHORT"],
    mark_price: float,
) -> dict[str, float | None]:
    symbol_config = _manual_symbol_config(config, symbol)
    leverage = _positive_float(symbol_config.get("leverage"), 1.0)
    max_position = _positive_float(symbol_config.get("max_position"), 0.5)

    wallet_balance = _positive_float(account.get("totalWalletBalance"), 0.0)
    unrealized = _float_or_none(account.get("totalUnrealizedProfit")) or 0.0
    account_equity = _float_or_none(account.get("totalMarginBalance"))
    if account_equity is None:
        account_equity = wallet_balance + unrealized
    available_balance = _float_or_none(account.get("availableBalance"))
    if available_balance is None:
        available_balance = max(0.0, account_equity)

    position_row = _manual_position_row(account, symbol)
    current_position_qty = _float_or_none(position_row.get("positionAmt")) if position_row else 0.0
    current_position_qty = current_position_qty or 0.0
    raw_notional = _float_or_none(position_row.get("notional")) if position_row else None
    current_position_notional = (
        abs(raw_notional) if raw_notional is not None else abs(current_position_qty * mark_price)
    )

    max_position_notional = max(0.0, account_equity * leverage * max_position)
    available_open_notional = max(0.0, available_balance * leverage)
    side_sign = 1 if side == "LONG" else -1
    same_direction = current_position_qty * side_sign > 0
    opposite_direction = current_position_qty * side_sign < 0

    if same_direction:
        remaining_position_notional = max(0.0, max_position_notional - current_position_notional)
        max_order_notional = min(remaining_position_notional, available_open_notional)
    elif opposite_direction:
        max_order_notional = current_position_notional + min(
            max_position_notional,
            available_open_notional,
        )
    else:
        max_order_notional = min(max_position_notional, available_open_notional)

    filters = exchange_filters or {}
    max_qty = _quantity_from_notional(max_order_notional, mark_price, filters)
    adjusted_max_notional = max_qty * mark_price

    min_qty = _decimal_or_none(filters.get("min_qty"))
    max_qty_filter = _decimal_or_none(filters.get("max_qty"))
    min_notional = _decimal_or_none(filters.get("min_notional"))
    step_size = _decimal_or_none(filters.get("step_size"))

    return {
        "mark_price": mark_price,
        "leverage": leverage,
        "max_position": max_position,
        "account_equity": account_equity,
        "available_balance": available_balance,
        "current_position_qty": current_position_qty,
        "current_position_notional": current_position_notional,
        "max_notional_usdt": adjusted_max_notional,
        "max_quantity": max_qty,
        "min_notional_usdt": float(min_notional) if min_notional is not None else None,
        "min_quantity": float(min_qty) if min_qty is not None else None,
        "max_exchange_quantity": float(max_qty_filter) if max_qty_filter is not None else None,
        "step_size": float(step_size) if step_size is not None else None,
    }


def _validate_manual_entry_quantity(
    *,
    quantity: float,
    side: Literal["LONG", "SHORT"],
    sizing: dict[str, float | None],
) -> None:
    mark_price = float(sizing["mark_price"] or 0.0)
    if quantity <= 0 or mark_price <= 0:
        raise HTTPException(status_code=422, detail="entry quantity must be greater than zero")

    min_quantity = sizing.get("min_quantity")
    if min_quantity is not None and quantity + 1e-12 < min_quantity:
        raise HTTPException(
            status_code=422,
            detail=f"quantity is below the exchange minimum ({min_quantity})",
        )

    order_notional = quantity * mark_price
    min_notional = sizing.get("min_notional_usdt")
    if min_notional is not None and order_notional + 1e-9 < min_notional:
        raise HTTPException(
            status_code=422,
            detail=f"order notional is below the exchange minimum ({min_notional} USDT)",
        )

    side_sign = 1 if side == "LONG" else -1
    current_qty = float(sizing["current_position_qty"] or 0.0)
    after_qty = current_qty + (quantity * side_sign)
    final_notional = abs(after_qty) * mark_price
    max_position_notional = (
        float(sizing["account_equity"] or 0.0)
        * float(sizing["leverage"] or 1.0)
        * float(sizing["max_position"] or 0.0)
    )
    if final_notional - max_position_notional > 1e-6:
        raise HTTPException(
            status_code=422,
            detail=f"position size exceeds the configured maximum ({max_position_notional:.2f} USDT)",
        )

    if current_qty * side_sign >= 0:
        opening_notional = order_notional
    else:
        opening_notional = max(0.0, abs(after_qty) * mark_price)
    available_open_notional = float(sizing["available_balance"] or 0.0) * float(
        sizing["leverage"] or 1.0
    )
    if opening_notional - available_open_notional > 1e-6:
        raise HTTPException(
            status_code=422,
            detail=f"entry notional exceeds available balance ({available_open_notional:.2f} USDT)",
        )


async def _manual_entry_sizing(
    client: Any,
    *,
    config: dict[str, Any] | None,
    symbol: str,
    side: Literal["LONG", "SHORT"],
) -> dict[str, float | None]:
    account = await client.fetch_account_info()
    mark_price = float(await client.fetch_mark_price(symbol))
    exchange_info = await client.fetch_exchange_info(symbol)
    filters = _manual_exchange_filters(exchange_info, symbol)
    return _manual_entry_sizing_from_state(
        config=config,
        account=account,
        exchange_filters=filters,
        symbol=symbol,
        side=side,
        mark_price=mark_price,
    )


def _manual_order_payload(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": order.get("orderId"),
        "client_order_id": order.get("clientOrderId"),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "type": order.get("type"),
        "status": order.get("status"),
        "orig_qty": order.get("origQty"),
        "executed_qty": order.get("executedQty"),
        "avg_price": order.get("avgPrice"),
        "price": order.get("price"),
    }


async def _resolve_manual_order_client(
    session: AsyncSession,
    *,
    user_id: str,
    job: Any,
    env: str,
) -> tuple[Any, bool]:
    if job.wallet_account_id is not None:
        wallet = await get_wallet_account(session, wallet_account_id=job.wallet_account_id)
        if wallet is None or wallet.user_id != user_id:
            raise HTTPException(status_code=404, detail="Wallet account not found")
        if wallet.env != env:
            raise HTTPException(
                status_code=422,
                detail=f"Wallet env({wallet.env}) does not match job env({env}).",
            )
        from binance.client_factory import BinanceClientFactoryError, get_client_factory

        try:
            client = await get_client_factory().get_trading_client(
                session,
                wallet_account_id=str(job.wallet_account_id),
            )
        except BinanceClientFactoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return client, False

    from binance.client import BinanceHTTPClient
    from common.crypto import get_crypto_service
    from control.repo import get_binance_credential

    cred = await get_binance_credential(session, user_id=user_id, env=env)
    if not cred:
        raise HTTPException(
            status_code=422,
            detail=f"Binance {env} API keys are not configured.",
        )
    try:
        crypto = get_crypto_service()
        api_key = crypto.decrypt(cred.api_key_enc)
        api_secret = crypto.decrypt(cred.api_secret_enc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail=f"Failed to decrypt Binance keys: {type(exc).__name__}",
        ) from exc
    base_url = {
        "mainnet": "https://fapi.binance.com",
        "testnet": "https://testnet.binancefuture.com",
    }[env]
    return BinanceHTTPClient(api_key=api_key, api_secret=api_secret, base_url=base_url), True


def _normalize_chat_user_id(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return "default"
    cleaned = re.sub(r"[^A-Za-z0-9._:-]+", "_", value)[:64].strip("_")
    return cleaned or "default"


def _chat_user_id_from_auth(user: AuthenticatedUser = Depends(require_auth)) -> str:
    return _normalize_chat_user_id(user.user_id)


def _chat_session_to_response(row: Any) -> StrategyChatSessionResponse:
    data = row.data_json if isinstance(row.data_json, dict) else {}
    messages = data.get("messages") if isinstance(data, dict) else None
    message_count = len(messages) if isinstance(messages, list) else 0
    return StrategyChatSessionResponse(
        session_id=str(row.session_id),
        title=str(row.title or "New chat"),
        data=data,
        message_count=message_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _chat_session_to_summary(row: Any) -> StrategyChatSessionSummary:
    """Lightweight conversion — metadata only, no data payload."""
    data = row.data_json if isinstance(row.data_json, dict) else {}
    messages = data.get("messages") if isinstance(data, dict) else None
    message_count = len(messages) if isinstance(messages, list) else 0
    return StrategyChatSessionSummary(
        session_id=str(row.session_id),
        title=str(row.title or "New chat"),
        message_count=message_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _verify_tmp_dir() -> Path:
    d = _repo_root() / ".verify_tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup_verify_temp(temp_path: Path) -> None:
    temp_path.unlink(missing_ok=True)
    pycache_dir = temp_path.parent / "__pycache__"
    if pycache_dir.is_dir():
        for f in pycache_dir.glob(f"{temp_path.stem}.cpython-*.pyc"):
            f.unlink(missing_ok=True)


def _strategy_dirs() -> list[Path]:
    settings = get_settings()
    parts = [p.strip() for p in (settings.strategy_dirs or ".").split(",") if p.strip()]
    root = _repo_root()
    return [(root / p).resolve() for p in parts]


def _local_capability_payload() -> dict[str, list[str]]:
    unsupported = [
        str(getattr(rule, "name", "")).strip() for rule in LOCAL_UNSUPPORTED_CAPABILITY_RULES
    ]
    return {
        "supported_data_sources": [
            str(v).strip() for v in LOCAL_SUPPORTED_DATA_SOURCES if str(v).strip()
        ],
        "supported_indicator_scopes": [
            str(v).strip() for v in LOCAL_SUPPORTED_INDICATOR_SCOPES if str(v).strip()
        ],
        "supported_context_methods": [
            str(v).strip() for v in LOCAL_SUPPORTED_CONTEXT_METHODS if str(v).strip()
        ],
        "unsupported_categories": [v for v in unsupported if v],
        "summary_lines": [
            str(v).strip() for v in local_capability_summary_lines() if str(v).strip()
        ],
    }


def _sanitize_strategy_filename(raw_name: str | None) -> str:
    raw = (raw_name or "").strip()
    if raw.endswith(".py"):
        raw = raw[:-3]
    raw = raw.replace("/", "_").replace("\\", "_")
    base = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    if not base:
        base = f"generated_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    if not base.endswith("_strategy"):
        base = f"{base}_strategy"
    if base == "generated_strategy":
        base = f"generated_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_strategy"
    return f"{base}.py"


def _unique_strategy_path(base_dir: Path, filename: str) -> Path:
    stem = filename[:-3] if filename.endswith(".py") else filename
    if not stem.endswith("_strategy"):
        stem = f"{stem}_strategy"
    candidate = base_dir / f"{stem}.py"
    if not candidate.exists():
        return candidate
    base = stem[: -len("_strategy")]
    for idx in range(2, 1000):
        alt = base_dir / f"{base}_{idx}_strategy.py"
        if not alt.exists():
            return alt
    raise HTTPException(status_code=409, detail="Could not allocate a unique strategy filename")


async def _list_strategies_for_user(
    *,
    session: AsyncSession,
    user: AuthenticatedUser,
) -> list[StrategyInfo]:
    # Always include built-in filesystem strategies as baseline
    root = _repo_root()
    files = list_strategy_files(_strategy_dirs())
    deduped: dict[str, StrategyInfo] = {
        p.name: StrategyInfo(name=p.name, path=str(p.relative_to(root))) for p in files
    }

    # Merge user's blob-stored strategies (overrides built-in if same name)
    storage = get_strategy_storage()
    if storage is not None:
        rows = await list_strategy_meta(session, user_id=user.user_id)
        for row in rows:
            deduped[row.strategy_name] = StrategyInfo(
                name=row.strategy_name,
                path=_logical_strategy_path(row.strategy_name),
            )

    return sorted(deduped.values(), key=lambda item: item.name)


async def _resolve_strategy_code_for_user(
    *,
    session: AsyncSession,
    user: AuthenticatedUser,
    path: str,
) -> tuple[str, str]:
    storage = get_strategy_storage()
    if storage is not None:
        try:
            strategy_name = _strategy_name_from_path(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        meta = await get_strategy_meta_by_name(
            session, user_id=user.user_id, strategy_name=strategy_name
        )
        if meta is not None:
            try:
                return strategy_name, storage.download_by_path(meta.blob_path)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=500, detail=f"Failed to read strategy object: {exc}"
                ) from exc

    root = _repo_root()
    dirs = _strategy_dirs()
    try:
        target = validate_strategy_path(repo_root=root, strategy_dirs=dirs, strategy_path=path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return target.name, target.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read strategy file: {exc}") from exc


async def _delete_strategy_for_user(
    *,
    session: AsyncSession,
    user: AuthenticatedUser,
    path: str,
) -> bool:
    storage = get_strategy_storage()
    if storage is not None:
        try:
            strategy_name = _strategy_name_from_path(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        meta = await get_strategy_meta_by_name(
            session, user_id=user.user_id, strategy_name=strategy_name
        )
        if meta is not None:
            deleted_blob = storage.delete_by_path(meta.blob_path)
            deleted_meta = await delete_strategy_meta_by_name(
                session,
                user_id=user.user_id,
                strategy_name=strategy_name,
            )
            return deleted_blob or deleted_meta

    root = _repo_root()
    dirs = _strategy_dirs()
    try:
        target = validate_strategy_path(repo_root=root, strategy_dirs=dirs, strategy_path=path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        target.unlink()
        return True
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {exc}") from exc


def _strip_code_fences(content: str) -> str:
    text = content.strip()
    if "```" not in text:
        return _strip_first_line_lang_tag(text)
    parts = text.split("```")
    if len(parts) >= 3:
        return _strip_first_line_lang_tag(parts[1].strip())
    return _strip_first_line_lang_tag(text)


def _strip_first_line_lang_tag(content: str) -> str:
    lines = content.splitlines()
    if not lines:
        return content
    first = lines[0].strip().lower()
    if first in ("python", "py", "python3"):
        return "\n".join(lines[1:]).lstrip("\n")
    return content


_INTAKE_ALLOWED_STATUSES = {
    "READY",
    "NEEDS_CLARIFICATION",
    "UNSUPPORTED_CAPABILITY",
    "OUT_OF_SCOPE",
}
_INTAKE_ALLOWED_INTENTS = {"OUT_OF_SCOPE", "STRATEGY_CREATE", "STRATEGY_MODIFY", "STRATEGY_QA"}


def _normalize_intake_payload(raw: dict[str, Any]) -> StrategyIntakeResponse:
    intent = str(raw.get("intent") or "STRATEGY_CREATE").strip().upper()
    if intent not in _INTAKE_ALLOWED_INTENTS:
        intent = "STRATEGY_CREATE"

    status = str(raw.get("status") or "NEEDS_CLARIFICATION").strip().upper()
    if status not in _INTAKE_ALLOWED_STATUSES:
        status = "NEEDS_CLARIFICATION"

    user_message = str(raw.get("user_message") or "").strip()
    if not user_message:
        if status == "OUT_OF_SCOPE":
            user_message = "이 입력은 트레이딩 전략 생성 요청으로 보기 어렵습니다."
        elif status == "UNSUPPORTED_CAPABILITY":
            user_message = "요청에는 현재 시스템에 없는 외부 연동 기능이 필요합니다."
        elif status == "NEEDS_CLARIFICATION":
            user_message = "전략 생성 전에 몇 가지 정보가 더 필요합니다."
        else:
            user_message = "요청이 명확하여 전략 생성을 진행할 수 있습니다."

    normalized_spec_raw = raw.get("normalized_spec")
    if not isinstance(normalized_spec_raw, dict):
        normalized_spec_raw = {}
    risk_raw = normalized_spec_raw.get("risk")
    risk = risk_raw if isinstance(risk_raw, dict) else {}
    normalized_spec = {
        "symbol": (str(normalized_spec_raw.get("symbol")).strip() or None)
        if normalized_spec_raw.get("symbol") is not None
        else None,
        "timeframe": (str(normalized_spec_raw.get("timeframe")).strip() or None)
        if normalized_spec_raw.get("timeframe") is not None
        else None,
        "entry_logic": (str(normalized_spec_raw.get("entry_logic")).strip() or None)
        if normalized_spec_raw.get("entry_logic") is not None
        else None,
        "exit_logic": (str(normalized_spec_raw.get("exit_logic")).strip() or None)
        if normalized_spec_raw.get("exit_logic") is not None
        else None,
        "risk": risk,
    }

    def _list_of_str(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            s = str(item).strip()
            if s:
                out.append(s)
        return out

    return StrategyIntakeResponse(
        intent=intent,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        user_message=user_message,
        normalized_spec=normalized_spec,  # type: ignore[arg-type]
        missing_fields=_list_of_str(raw.get("missing_fields")),
        unsupported_requirements=_list_of_str(raw.get("unsupported_requirements")),
        clarification_questions=_list_of_str(raw.get("clarification_questions")),
        assumptions=_list_of_str(raw.get("assumptions")),
        development_requirements=_list_of_str(raw.get("development_requirements")),
    )


async def _run_intake(
    client: LLMClient, prompt: str, messages: list[dict[str, str]] | None
) -> StrategyIntakeResponse:
    intake_raw = await client.intake_strategy(prompt, messages=messages)
    if not intake_raw:
        return StrategyIntakeResponse(
            intent="STRATEGY_CREATE",
            status="NEEDS_CLARIFICATION",
            user_message="입력 해석에 실패했습니다. 전략 조건을 더 구체적으로 적어주세요.",
            normalized_spec=None,
            missing_fields=[],
            unsupported_requirements=[],
            clarification_questions=[
                "어떤 심볼로 거래할까요? (예: BTCUSDT)",
                "진입 조건을 한 줄로 적어주세요.",
                "청산 조건을 한 줄로 적어주세요.",
            ],
            assumptions=[],
            development_requirements=[],
        )
    return _normalize_intake_payload(intake_raw)


def _verify_strategy_load(strategy_path: Path, repo_root: Path) -> None:
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    module_name = f"strategy_verify_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, strategy_path)
    if not spec or not spec.loader:
        raise ValueError(f"Failed to load strategy file: {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    strategy_class = None
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
            strategy_class = obj
            break
    if strategy_class is None:
        raise ValueError(f"No *Strategy class found in {strategy_path}")
    strategy_class()


def _verify_strategy_backtest(strategy_path: Path, repo_root: Path) -> None:
    rel = strategy_path.relative_to(repo_root)
    cmd = [
        "uv",
        "run",
        "python",
        "scripts/run_backtest.py",
        str(rel),
        "--symbol",
        "BTCUSDT",
        "--candle-interval",
        "1h",
        "--start-date",
        "2024-06-01",
        "--end-date",
        "2024-06-03",
    ]
    result = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        stderr = result.stderr or result.stdout or ""
        raise ValueError(f"Backtest failed: {stderr[:2000]}")


def create_app() -> FastAPI:
    settings = get_settings()
    engine = create_async_engine(settings.effective_database_url)
    session_maker = create_session_maker(engine)
    futures_symbols_cache: dict[str, Any] = {"expires_at": 0.0, "symbols": []}

    app = FastAPI(title="LLMTrader API", version="0.1.0")
    app.state.engine = engine
    app.state.session_maker = session_maker

    _logger = logging.getLogger("api")

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        _logger.error(
            "Unhandled exception on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
        )
        from sqlalchemy.exc import InterfaceError, OperationalError

        if isinstance(exc, (OperationalError, InterfaceError, OSError)):
            return JSONResponse(
                status_code=503,
                content={"detail": f"Database temporarily unavailable: {type(exc).__name__}"},
            )
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal server error: {type(exc).__name__}"},
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    set_session_maker(session_maker)

    _keepalive_task: asyncio.Task[None] | None = None
    _runner_task: asyncio.Task[None] | None = None
    _runner_worker: Any = None
    _db_init_task: asyncio.Task[None] | None = None

    app.state.db_ready = False

    async def _db_keepalive(interval: int = 60) -> None:
        """Periodically send SELECT 1 to prevent idle DB connection termination."""
        while True:
            await asyncio.sleep(interval)
            try:
                async with session_maker() as session:
                    await session.execute(text("SELECT 1"))
            except Exception as exc:
                _logger.warning("DB keep-alive ping failed: %s", exc)

    async def _db_init_loop(
        base_delay: float = 5.0,
        max_delay: float = 60.0,
        attempt_timeout: float = 45.0,
    ) -> None:
        """Apply alembic upgrade + init_db off the startup critical path.

        Runs as a background task so uvicorn can finish startup immediately and
        serve the liveness/startup probes even when PG is unreachable (otherwise
        a blocking init would stall startup, fail the probes, and trigger a
        Container Apps crash loop). Retries indefinitely with capped backoff so
        the API self-heals once PG becomes reachable again. DB-dependent
        endpoints surface 503 via middleware until ``app.state.db_ready`` is set.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                async with asyncio.timeout(attempt_timeout):
                    if settings.auto_alembic_upgrade:
                        _logger.info(
                            "Applying database migrations (alembic upgrade head, attempt %d)...",
                            attempt,
                        )
                        await asyncio.to_thread(run_alembic_upgrade_head)
                    await init_db(engine)
                app.state.db_ready = True
                _logger.info("Database init complete (attempt %d); db_ready=True", attempt)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                delay = min(base_delay * attempt, max_delay)
                _logger.warning(
                    "Background DB init failed (attempt %d): %s; retrying in %.0fs",
                    attempt,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    @app.on_event("startup")
    async def _startup() -> None:
        nonlocal _keepalive_task, _runner_task, _runner_worker, _db_init_task

        # Kick off DB migrations/init in the background so a slow or unreachable
        # DB does not block uvicorn startup (which would fail the liveness/startup
        # probes and cause a crash loop). The loop self-heals when PG recovers.
        _db_init_task = asyncio.create_task(_db_init_loop())
        _keepalive_task = asyncio.create_task(_db_keepalive())
        _logger.info("DB init + keep-alive background tasks started (non-blocking startup)")

        if settings.embedded_runner:
            from runner.worker import RunnerWorker

            _runner_worker = RunnerWorker(
                repo_root=Path(__file__).resolve().parents[2],
                session_maker=session_maker,
                poll_interval_ms=settings.runner_poll_interval_ms,
                live_concurrency=settings.runner_live_concurrency,
                role=settings.runner_role,
            )
            _runner_task = asyncio.create_task(_runner_worker.run_forever())
            _logger.info(
                "Embedded runner started (role=%s, live_concurrency=%d)",
                settings.runner_role,
                settings.runner_live_concurrency,
            )

        if settings.auto_sweep_enabled:
            try:
                from live.auto_sweep_engine import start_engine as _start_auto_sweep

                await _start_auto_sweep(session_maker)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("Auto-sweep engine failed to start: %s", exc)

        # 펀딩비 차익거래 엔진 자동 복원: 재배포·재시작 후에도 desired_running=true인
        # 엔진을 다시 띄워 거래소 포지션이 고아가 되지 않게 한다. DB가 준비될 때까지
        # 대기·jitter·heartbeat 체크가 포함되어 startup을 막지 않는다.
        try:
            from live.funding_arbitrage_engine import (
                restore_engines_on_startup as _restore_funding_arb,
            )

            asyncio.create_task(
                _restore_funding_arb(session_maker),
                name="funding_arb_auto_restore",
            )
            _logger.info("Funding-arb auto-restore task scheduled")
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Funding-arb auto-restore failed to schedule: %s", exc)

        # 김프 델타-중립 엔진 자동 복원: 재배포 후에도 desired_running=true인 엔진을
        # 다시 띄워 업비트/바이낸스 양다리 포지션이 고아가 되지 않게 한다.
        try:
            from live.kimp_neutral_engine import (
                restore_engines_on_startup as _restore_kimp_arb,
            )

            asyncio.create_task(
                _restore_kimp_arb(session_maker),
                name="kimp_arb_auto_restore",
            )
            _logger.info("Kimp-arb auto-restore task scheduled")
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Kimp-arb auto-restore failed to schedule: %s", exc)

        # 김프 스냅샷 1분 콜렉터: 백테스트/시계열 차트/30일 통계의 원천 데이터를
        # 적재한다. 외부 API/DB 실패는 콜렉터 내부에서 로깅만 하고 다음 사이클로
        # 넘어가므로 API startup 을 막지 않는다.
        try:
            from live.kimp_history import start_collector as _start_kimp_collector

            _start_kimp_collector(session_maker)
            _logger.info("Kimp snapshot collector scheduled (60s interval)")
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Kimp snapshot collector failed to schedule: %s", exc)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        nonlocal _keepalive_task, _runner_task, _runner_worker, _db_init_task
        try:
            from live.auto_sweep_engine import stop_engine as _stop_auto_sweep

            await _stop_auto_sweep()
        except Exception:  # noqa: BLE001
            pass
        try:
            from live.kimp_history import stop_collector as _stop_kimp_collector

            await _stop_kimp_collector()
        except Exception:  # noqa: BLE001
            pass
        if _runner_worker is not None:
            _runner_worker._shutting_down.set()
            _logger.info("Embedded runner shutdown requested")
        if _runner_task is not None:
            _runner_task.cancel()
            _logger.info("Embedded runner task cancelled")
        if _db_init_task is not None:
            _db_init_task.cancel()
            _logger.info("DB init task cancelled")
        if _keepalive_task:
            _keepalive_task.cancel()
            _logger.info("DB keep-alive task stopped")

    async def _db_session() -> AsyncIterator[AsyncSession]:
        from sqlalchemy.exc import InterfaceError, OperationalError

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with session_maker() as session:
                    yield session
                    return
            except (OperationalError, InterfaceError, OSError) as exc:
                if attempt < max_retries:
                    _logger.warning(
                        "DB session error (attempt %d/%d): %s", attempt + 1, max_retries + 1, exc
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    _logger.error("DB session failed after %d attempts: %s", max_retries + 1, exc)
                    raise

    strategy_pipeline_version = "multi-agent-v1"

    def _rate(num: int, den: int) -> float:
        if den <= 0:
            return 0.0
        return round(float(num) / float(den), 4)

    def _top_counts(counter: Counter[str], limit: int = 5) -> list[CountItem]:
        return [CountItem(name=name, count=count) for name, count in counter.most_common(limit)]

    async def _record_strategy_quality(
        *,
        request_id: uuid.UUID,
        endpoint: str,
        user_prompt_len: int,
        message_count: int,
        intake: StrategyIntakeResponse | None = None,
        generation_attempted: bool | None = None,
        generation_success: bool | None = None,
        verification_passed: bool | None = None,
        repaired: bool | None = None,
        repair_attempts: int = 0,
        model_used: str | None = None,
        error_stage: str | None = None,
        error_message: str | None = None,
        duration_ms: int = 0,
        meta_json: dict[str, Any] | None = None,
    ) -> None:
        try:
            async with session_maker() as session:
                await create_strategy_quality_log(
                    session,
                    request_id=request_id,
                    pipeline_version=strategy_pipeline_version,
                    endpoint=endpoint,
                    user_prompt_len=max(0, int(user_prompt_len)),
                    message_count=max(0, int(message_count)),
                    intent=(intake.intent if intake else None),
                    status=(intake.status if intake else None),
                    missing_fields=list(intake.missing_fields) if intake else [],
                    unsupported_requirements=list(intake.unsupported_requirements)
                    if intake
                    else [],
                    development_requirements=list(intake.development_requirements)
                    if intake
                    else [],
                    generation_attempted=generation_attempted,
                    generation_success=generation_success,
                    verification_passed=verification_passed,
                    repaired=repaired,
                    repair_attempts=max(0, int(repair_attempts)),
                    model_used=model_used,
                    error_stage=error_stage,
                    error_message=(str(error_message)[:2000] if error_message else None),
                    duration_ms=max(0, int(duration_ms)),
                    meta_json=meta_json,
                )
                await session.commit()
        except Exception:
            # Quality logging must never break main workflow.
            return

    @app.get("/api/health/live")
    async def health_live() -> dict[str, str]:
        """Liveness probe: process is up, no external dependency check.

        Used by Container Apps Liveness/Startup probes so a brief PG outage
        does not cause the replica to be killed. Always returns 200 unless
        the asyncio event loop itself is dead.
        """
        return {"status": "ok"}

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Readiness probe: includes DB SELECT 1.

        Returns 200 with db_ok=false when DB is temporarily unavailable;
        callers (and the readiness probe) treat that as "not ready".
        """
        try:
            async with asyncio.timeout(5):
                async with session_maker() as session:
                    await session.execute(text("SELECT 1"))
            return HealthResponse(status="ok", db_ok=True, db_error=None)
        except Exception as exc:  # noqa: BLE001
            return HealthResponse(status="error", db_ok=False, db_error=str(exc))

    @app.get(
        "/api/binance/account/summary",
        response_model=BinanceAccountSummaryResponse,
    )
    async def binance_account_summary(
        env: str = Query("mainnet", pattern="^(mainnet|testnet)$"),
        wallet_account_id: str | None = Query(default=None),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> BinanceAccountSummaryResponse:
        def to_response(data: dict[str, Any]) -> BinanceAccountSummaryResponse:
            assets = [BinanceAssetBalance(**a) for a in data.get("assets", [])]
            positions = [BinancePositionSummary(**p) for p in data.get("positions", [])]

            update_time_raw = data.get("update_time")
            update_time = datetime.now()
            if isinstance(update_time_raw, str):
                try:
                    update_time = datetime.fromisoformat(update_time_raw)
                except (ValueError, TypeError):
                    pass

            return BinanceAccountSummaryResponse(
                configured=data.get("configured", False),
                connected=data.get("connected", False),
                mode=data.get("mode", "testnet"),
                base_url=data.get("base_url", ""),
                total_wallet_balance=data.get("total_wallet_balance"),
                total_wallet_balance_btc=data.get("total_wallet_balance_btc"),
                total_unrealized_profit=data.get("total_unrealized_profit"),
                total_margin_balance=data.get("total_margin_balance"),
                available_balance=data.get("available_balance"),
                can_trade=data.get("can_trade"),
                update_time=update_time,
                assets=assets,
                positions=positions,
                error=data.get("error"),
            )

        base_url = {
            "mainnet": "https://fapi.binance.com",
            "testnet": "https://testnet.binancefuture.com",
        }[env]

        if wallet_account_id:
            try:
                wallet_id = uuid.UUID(wallet_account_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid wallet_account_id") from exc

            wallet = await get_wallet_account(session, wallet_account_id=wallet_id)
            if wallet is None or wallet.user_id != user.user_id:
                raise HTTPException(status_code=404, detail="Wallet not found")
            if wallet.env != env:
                raise HTTPException(
                    status_code=400,
                    detail=f"Wallet env {wallet.env} does not match requested env {env}",
                )
            wallet_role = wallet.role.value if hasattr(wallet.role, "value") else wallet.role
            if wallet_role == WalletRole.SUB.value and not (wallet.enabled_wallets or {}).get(
                "futures_um"
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Selected sub-account does not have USD-M Futures enabled",
                )

            from binance.client_factory import BinanceClientFactoryError, get_client_factory
            from runner.account_snapshot import fetch_snapshot_from_client

            try:
                client = await get_client_factory().get_trading_client(
                    session,
                    wallet_account_id=str(wallet_id),
                )
            except BinanceClientFactoryError as exc:
                return BinanceAccountSummaryResponse(
                    configured=True,
                    connected=False,
                    mode=env,
                    base_url=base_url,
                    error=str(exc),
                )

            data = await fetch_snapshot_from_client(
                client,
                base_url=getattr(client, "base_url", base_url),
            )
            return to_response(data)

        from control.repo import get_binance_credential

        cred = await get_binance_credential(session, user_id=user.user_id, env=env)
        if not cred:
            return BinanceAccountSummaryResponse(
                configured=False,
                connected=False,
                mode=env,
                base_url="",
                error=f"Binance {env} API keys are not configured. Go to Settings to set up your keys.",
            )

        from common.crypto import get_crypto_service

        try:
            crypto = get_crypto_service()
            api_key = crypto.decrypt(cred.api_key_enc)
            api_secret = crypto.decrypt(cred.api_secret_enc)
        except Exception as exc:  # noqa: BLE001
            return BinanceAccountSummaryResponse(
                configured=True,
                connected=False,
                mode=env,
                base_url=base_url,
                error=f"Failed to decrypt keys: {type(exc).__name__}",
            )

        from runner.account_snapshot import _fetch_snapshot

        data = await _fetch_snapshot(api_key=api_key, api_secret=api_secret, base_url=base_url)
        return to_response(data)

    @app.get("/api/portfolio/summary", response_model=PortfolioSummaryResponse)
    async def portfolio_summary(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> PortfolioSummaryResponse:
        """전체 AUM + 전략 카테고리별 자산 배분 요약."""

        from control.repo import get_user_profile

        now = datetime.now(UTC)
        futures_balance = 0.0
        futures_unrealized = 0.0

        profile = await get_user_profile(session, user_id=user.user_id)
        from control.repo import get_binance_credential

        mainnet_cred = (
            await get_binance_credential(session, user_id=user.user_id, env="mainnet")
            if profile
            else None
        )
        if mainnet_cred:
            snapshot = await get_account_snapshot(session, user_id=user.user_id)
            if snapshot and isinstance(snapshot.data, dict):
                d = snapshot.data
                futures_balance = float(d.get("total_wallet_balance") or 0)
                futures_unrealized = float(d.get("total_unrealized_profit") or 0)

        # Running live jobs → Directional_Alpha allocated capital estimate
        running_jobs = await list_jobs(
            session,
            user_id=user.user_id,
            job_type=JobType.LIVE,
            status=JobStatus.RUNNING,
            limit=64,
        )
        directional_alloc = sum(
            float((j.config or {}).get("initial_balance") or 0) for j in running_jobs
        )

        total_aum = futures_balance
        realized_today = 0.0

        cash = max(0.0, futures_balance - directional_alloc)
        slices: list[AllocationSlice] = []
        if total_aum > 0:
            if directional_alloc > 0:
                slices.append(
                    AllocationSlice(
                        category="Directional_Alpha",
                        allocated_usdt=directional_alloc,
                        pct=round(directional_alloc / total_aum * 100, 1),
                    )
                )
            if cash > 0:
                slices.append(
                    AllocationSlice(
                        category="Cash",
                        allocated_usdt=cash,
                        pct=round(cash / total_aum * 100, 1),
                    )
                )
        else:
            slices.append(AllocationSlice(category="Cash", allocated_usdt=0.0, pct=100.0))

        return PortfolioSummaryResponse(
            total_aum_usdt=total_aum,
            total_unrealized_pnl=futures_unrealized,
            total_realized_pnl_today=realized_today,
            wallets=[
                WalletSnapshot(
                    wallet="futures",
                    balance_usdt=futures_balance,
                    unrealized_pnl=futures_unrealized,
                )
            ],
            allocation=slices,
            as_of=now,
        )

    @app.get("/api/strategy-modules", response_model=StrategyModuleCatalogResponse)
    async def strategy_module_catalog(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> StrategyModuleCatalogResponse:
        """앱스토어형 전략 모듈 카탈로그 (현재 실행 상태 포함)."""
        running_jobs = await list_jobs(
            session,
            user_id=user.user_id,
            job_type=JobType.LIVE,
            status=JobStatus.RUNNING,
            limit=64,
        )
        running_ids = [str(j.job_id) for j in running_jobs]
        directional_alloc = sum(
            float((j.config or {}).get("initial_balance") or 0) for j in running_jobs
        )
        modules = [
            StrategyModuleStatus(
                module_id="directional_alpha",
                name="Directional Alpha",
                category="Directional_Alpha",
                enabled=len(running_ids) > 0,
                allocated_usdt=directional_alloc,
                running_job_ids=running_ids,
                status="running" if running_ids else "idle",
            ),
            StrategyModuleStatus(
                module_id="funding_arbitrage",
                name="Funding Rate Arbitrage",
                category="Market_Neutral_Arbitrage",
                enabled=False,
                allocated_usdt=0.0,
                status="idle",
            ),
            StrategyModuleStatus(
                module_id="simple_earn",
                name="Simple Earn Auto-Deposit",
                category="Yield_Earn",
                enabled=False,
                allocated_usdt=0.0,
                status="idle",
            ),
        ]
        return StrategyModuleCatalogResponse(modules=modules)

    @app.get("/api/funding-arb/status", response_model=FundingArbitrageStatusResponse)
    async def funding_arb_status(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> FundingArbitrageStatusResponse:
        """펀딩비 차익거래 봇 현재 상태 (모든 replica에서 일관)."""
        from live.funding_arbitrage_engine import get_engine_status_persisted

        return await get_engine_status_persisted(session, user.user_id)

    @app.get("/api/funding-arb/screener", response_model=FundingScreenerResponse)
    async def funding_arb_screener(
        top_n: int = 20,
        env: str = "mainnet",
        user: AuthenticatedUser = Depends(require_auth),  # noqa: ARG001
    ) -> FundingScreenerResponse:
        """현재 펀딩비가 양수인 현물·선물 동시 상장 종목의 Top-N 스크리너.

        유니버스 정의:
          ``현물 USDT 상장(TRADING)`` ∩ ``선물 USDT 상장(premiumIndex)`` 전체.
          Redis 통계 표본이 없는 종목도 포함하여 시장 전체 후보를 노출한다.
          단 펀딩비 ≤ 0인 종목은 차익거래 손실 구조라 자동 제외한다.

        종목별 score:
          ``score = current_rate / (ROUNDTRIP / half_life)``
          half-life 통계가 있을 때만 산출되며, 없으면 ``None``. score ≥ 1.0×에서 진입.

        부가 컬럼(정렬용):
          - ``quote_volume_24h``: Binance mainnet 현물 24h 거래대금(USDT)
          - ``market_cap_usd``: CoinGecko 시가총액(USD)

        ``env`` 가 ``testnet`` 이면 데모 트레이딩(testnet.binancefuture.com 선물 +
        demo-api.binance.com 현물)의 실제 펀딩비·상장을 반영한다. Testnet 펀딩비는
        운영망과 부호·크기가 전혀 다르므로(예: 운영망 +0.01% 인데 testnet −0.34%),
        엔진이 실제로 보게 될 값으로 스크리닝해야 "양수처럼 보이는데 진입 안 됨"
        혼란을 막는다. half-life 통계는 운영망 OU 적합 결과를 그대로 사용한다
        (testnet은 통계 표본이 없음).
        """
        import json as _json

        is_testnet = env == "testnet"
        fut_base = "https://testnet.binancefuture.com" if is_testnet else "https://fapi.binance.com"

        ROUNDTRIP = _FUNDING_ROUNDTRIP_COST
        DEFAULT_INTERVAL_H = 8.0
        PPY = (365 * 24) / DEFAULT_INTERVAL_H  # 1095 (정산 횟수/년)

        # 1) 통계(Redis, optional). 통계가 없는 종목도 유니버스에 포함되어야 하므로
        #    Redis 부재 시에도 에러 대신 빈 dict로 진행한다.
        stats_map: dict[str, dict] = {}
        rd = _make_funding_redis()
        if rd is not None:
            try:
                univ_raw = rd.get("funding:stats:_universe")
                stat_symbols: list[str] = []
                if univ_raw:
                    parsed = _json.loads(univ_raw)
                    if isinstance(parsed, dict):
                        stat_symbols = list(parsed.get("symbols", []))
                    else:
                        stat_symbols = list(parsed)
                if stat_symbols:
                    keys = [f"funding:stats:{sym}" for sym in stat_symbols]
                    raw_vals = rd.mget(keys)
                    for sym, raw in zip(stat_symbols, raw_vals):
                        if raw:
                            try:
                                stats_map[sym] = _json.loads(raw)
                            except Exception:  # noqa: BLE001
                                pass
            except Exception as exc:  # noqa: BLE001
                _log.warning("Funding stats fetch failed: %s", exc)

        # 2) 선물 premiumIndex (전체 펀딩비)
        try:
            async with httpx.AsyncClient(base_url=fut_base, timeout=10.0) as client:
                resp = await client.get("/fapi/v1/premiumIndex")
                resp.raise_for_status()
                rates: dict[str, float] = {
                    row["symbol"]: float(row.get("lastFundingRate", 0))
                    for row in resp.json()
                    if isinstance(row, dict)
                }
        except Exception as exc:  # noqa: BLE001
            return FundingScreenerResponse(
                items=[],
                roundtrip_cost_pct=ROUNDTRIP * 100,
                error=f"Binance API 오류: {exc}",
                as_of=datetime.now(UTC),
            )

        # 3) 현물 상장 + 24h 거래대금 + 시가총액 (모두 캐시)
        spot_symbols = await _fetch_tradable_spot_symbols(testnet=is_testnet)
        quote_volumes = await _fetch_spot_24h_quote_volume(testnet=is_testnet)
        market_caps = await _fetch_market_caps()

        # 4) 유니버스 = 현물 ∩ 선물 (USDT). 둘 다 상장된 종목만 차익거래 가능.
        if not spot_symbols:
            return FundingScreenerResponse(
                items=[],
                roundtrip_cost_pct=ROUNDTRIP * 100,
                error="현물 상장 목록을 가져오지 못했습니다.",
                as_of=datetime.now(UTC),
            )
        universe = sorted(sym for sym in rates if sym.endswith("USDT") and sym in spot_symbols)

        items: list[FundingScreenerItem] = []
        for sym in universe:
            current_rate = rates.get(sym, 0.0)
            # 펀딩비가 0 이하인 종목은 현물 롱 + 선물 숏 구조에서 손실 → 제외.
            if current_rate <= 0:
                continue

            stat = stats_map.get(sym, {})
            hl_raw = stat.get("half_life_settlements")
            hl: float | None
            entry_threshold_pct: float | None
            score: float | None
            try:
                hl_val = float(hl_raw) if hl_raw is not None else 0.0
            except (TypeError, ValueError):
                hl_val = 0.0
            if hl_val > 0:
                hl = round(hl_val, 2)
                entry_threshold_pct = round((ROUNDTRIP / hl_val) * 100, 5)
                score = round((current_rate * 100) / entry_threshold_pct, 2)
            else:
                hl = None
                entry_threshold_pct = None
                score = None

            base = sym.removesuffix("USDT")
            items.append(
                FundingScreenerItem(
                    symbol=sym,
                    current_rate_pct=round(current_rate * 100, 5),
                    annualized_pct=round(current_rate * PPY * 100, 2),
                    half_life_settlements=hl,
                    entry_threshold_pct=entry_threshold_pct,
                    score=score,
                    avg_rate_pct=(round(float(stat.get("avg_rate", 0.0)), 5) if stat else None),
                    n_samples=int(stat.get("n_samples", 0)) if stat else 0,
                    quote_volume_24h=(quote_volumes.get(sym) if quote_volumes else None),
                    market_cap_usd=market_caps.get(base.upper()) if market_caps else None,
                )
            )

        # 기본 정렬: score 내림차순(None은 뒤). 프론트엔드가 컬럼별로 재정렬한다.
        items.sort(
            key=lambda x: x.score if x.score is not None else float("-inf"),
            reverse=True,
        )
        # top_n은 200으로 상한(과도한 페이로드 방지).
        capped = max(1, min(int(top_n or 20), 200))
        return FundingScreenerResponse(
            items=items[:capped],
            roundtrip_cost_pct=round(ROUNDTRIP * 100, 2),
            as_of=datetime.now(UTC),
        )

    @app.get(
        "/api/funding-arb/symbol-detail",
        response_model=FundingSymbolDetailResponse,
    )
    async def funding_arb_symbol_detail(
        symbol: str,
        user: AuthenticatedUser = Depends(require_auth),  # noqa: ARG001
    ) -> FundingSymbolDetailResponse:
        """심볼별 펀딩비 상세 통계 + 시계열 (계약 상장 이후 전체 기간).

        Binance USD-M ``/fapi/v1/fundingRate`` 운영망에서 전체 기간을
        페이지네이션으로 가져와 다음을 계산한다:
          - 윈도우 평균(7일/30일/180일/365일/전체)
          - 전체 기간 내 최대/최소 펀딩비와 그 시점
          - 차트용 시계열(전체 기간을 균등 다운샘플하여 ≤ 500 포인트)

        결과는 1시간 캐시한다(펀딩 정산이 8시간 주기이므로 충분).
        Testnet은 펀딩비 이력이 의미 없으므로 항상 운영망(fapi.binance.com)
        을 조회한다.
        """

        sym = (symbol or "").strip().upper()
        if not sym or not sym.isalnum() or len(sym) > 32:
            return FundingSymbolDetailResponse(
                symbol=sym,
                as_of=datetime.now(UTC),
                n_samples=0,
                window_stats=[],
                max=None,
                min=None,
                series=[],
                error="잘못된 심볼입니다.",
            )

        return await _funding_symbol_detail_cached(sym)

    @app.post("/api/funding-arb/start", response_model=FundingArbitrageStatusResponse)
    async def funding_arb_start(
        params: FundingArbitrageParams,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> FundingArbitrageStatusResponse:
        """펀딩비 차익거래 봇 시작."""
        from common.crypto import get_crypto_service
        from control.repo import get_binance_credential
        from live.funding_arbitrage_engine import (
            get_engine_status_persisted,
            start_engine,
        )

        crypto = get_crypto_service()

        # 멀티 replica 중복 진입 방지: 이미 (다른 replica 포함) 실행 중이면 거부.
        current = await get_engine_status_persisted(session, user.user_id)
        if current.running:
            raise HTTPException(
                status_code=409,
                detail=f"이미 실행 중입니다 (symbol={current.symbol}). 먼저 정지하세요.",
            )

        if params.env == "testnet":
            fut_cred = await get_binance_credential(session, user_id=user.user_id, env="testnet")
            spot_cred = fut_cred
            if not fut_cred:
                raise HTTPException(
                    status_code=400, detail="Testnet(Demo) API 키가 설정되지 않았습니다."
                )
            is_testnet = True
        else:
            fut_cred = await get_binance_credential(session, user_id=user.user_id, env="mainnet")
            spot_cred = fut_cred
            if not fut_cred:
                raise HTTPException(status_code=400, detail="Mainnet API 키가 설정되지 않았습니다.")
            is_testnet = False

        futures_api_key = crypto.decrypt(fut_cred.api_key_enc)
        futures_api_secret = crypto.decrypt(fut_cred.api_secret_enc)
        spot_api_key = crypto.decrypt(spot_cred.api_key_enc)
        spot_api_secret = crypto.decrypt(spot_cred.api_secret_enc)

        # hold_days가 설정된 경우 Redis AR(1)/OU 통계로 deadband를 자동 계산
        if params.hold_days is not None:
            resolved = _resolve_funding_deadband(params.symbol, params.hold_days)
            if resolved is not None:
                entry_pct, exit_pct = resolved
                params = params.model_copy(
                    update={
                        "entry_deadband_pct": entry_pct,
                        "exit_deadband_pct": exit_pct,
                    }
                )
                logging.getLogger("api").info(
                    "Dynamic deadband resolved for %s hold_days=%d: entry=%.5f%% exit=%.5f%%",
                    params.symbol,
                    params.hold_days,
                    entry_pct,
                    exit_pct,
                )
            else:
                logging.getLogger("api").warning(
                    "No Redis stats for %s — using provided deadband values", params.symbol
                )

        await start_engine(
            user_id=user.user_id,
            params=params,
            futures_api_key=futures_api_key,
            futures_api_secret=futures_api_secret,
            spot_api_key=spot_api_key,
            spot_api_secret=spot_api_secret,
            is_testnet=is_testnet,
            session_maker=session_maker,
        )
        return await get_engine_status_persisted(session, user.user_id)

    @app.post("/api/funding-arb/stop", response_model=FundingArbitrageStatusResponse)
    async def funding_arb_stop(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> FundingArbitrageStatusResponse:
        """펀딩비 차익거래 봇 정지 (어느 replica에서 실행 중이든 정지)."""
        from live.funding_arbitrage_engine import get_engine_status_persisted, stop_engine

        await stop_engine(user.user_id, session_maker=session_maker)
        return await get_engine_status_persisted(session, user.user_id)

    # ── Kimchi Premium (김프) Arbitrage ────────────────────────────
    # Phase 1: 모니터링(읽기 전용) — USDT/KRW 기준가 / 스크리너 / 시계열 히스토리

    @app.get("/api/kimp-arb/fx", response_model=KimpFxRateResponse)
    async def kimp_fx_rate(
        force_refresh: bool = False,
        _: AuthenticatedUser = Depends(require_auth),
    ) -> KimpFxRateResponse:
        """USDT/KRW 기준가 (Upbit KRW-USDT).

        ``force_refresh`` 는 기존 클라이언트 호환성을 위해 유지하지만 Upbit 공개 시세는
        매 호출 최신 가격을 조회한다.
        """
        from live.kimp_calculator import get_usdt_krw_rate

        _ = force_refresh
        rate = await get_usdt_krw_rate()
        return KimpFxRateResponse(
            rate=rate.rate,
            source=rate.source,
            fetched_at=rate.fetched_at,
            stale=rate.stale,
        )

    def _parse_kimp_symbols(symbols: str | None) -> list[str] | None:
        if not symbols:
            return None
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        return requested or None

    async def _build_kimp_screener_response(
        session: AsyncSession,
        symbols: str | None,
    ) -> KimpScreenerResponse:
        from live.kimp_calculator import classify_kimp_signal, compute_kimp_snapshot
        from live.fx_feed import get_fx_rate
        from live.kimp_history import window_stats_bulk
        from live.kimp_universe import get_kimp_universe

        requested = _parse_kimp_symbols(symbols) or await get_kimp_universe()
        snapshot = await compute_kimp_snapshot(requested)
        bank_fx = None
        bank_fx_error: str | None = None
        try:
            bank_fx = await get_fx_rate()
        except RuntimeError as exc:
            bank_fx_error = f"USD/KRW: {exc}"

        stats_by_symbol = await window_stats_bulk(
            session, [row.symbol for row in snapshot.rows], days=30
        )

        items: list[KimpScreenerItem] = []
        for row in snapshot.rows:
            stats = stats_by_symbol.get(row.symbol, {})
            mean_pct = stats.get("mean")
            std_pct = stats.get("std")
            n = int(stats.get("n") or 0)
            z = None
            if mean_pct is not None and std_pct is not None and float(std_pct) > 0:
                z = (row.kimp_pct - float(mean_pct)) / float(std_pct)
            bank_kimp_pct = None
            if bank_fx is not None and bank_fx.rate > 0:
                bank_denom = row.binance_usdt_price * bank_fx.rate
                if bank_denom > 0:
                    bank_kimp_pct = (row.upbit_krw_price / bank_denom) - 1.0
            spot_bank_kimp_pct = None
            if (
                bank_fx is not None
                and bank_fx.rate > 0
                and row.binance_spot_price
                and row.binance_spot_price > 0
            ):
                spot_bank_denom = row.binance_spot_price * bank_fx.rate
                if spot_bank_denom > 0:
                    spot_bank_kimp_pct = (row.upbit_krw_price / spot_bank_denom) - 1.0
            items.append(
                KimpScreenerItem(
                    symbol=row.symbol,
                    upbit_krw_price=row.upbit_krw_price,
                    binance_usdt_price=row.binance_usdt_price,
                    binance_spot_price=row.binance_spot_price,
                    usdt_krw_rate=row.usdt_krw_rate,
                    usd_krw_rate=bank_fx.rate if bank_fx is not None else None,
                    kimp_pct=row.kimp_pct,
                    bank_kimp_pct=bank_kimp_pct,
                    spot_kimp_pct=row.spot_kimp_pct,
                    spot_bank_kimp_pct=spot_bank_kimp_pct,
                    mean_30d_pct=float(mean_pct) if mean_pct is not None else None,
                    std_30d_pct=float(std_pct) if std_pct is not None else None,
                    zscore_30d=z,
                    n_samples_30d=n,
                    funding_rate_pct=round(row.funding_rate * 100, 5),
                    funding_interval_hours=row.funding_interval_hours,
                    next_funding_time=row.next_funding_time,
                    upbit_quote_volume_krw=row.upbit_quote_volume_krw,
                    signal=classify_kimp_signal(row.kimp_pct, z),
                )
            )

        rate_payload = KimpFxRateResponse(
            pair="USDT/KRW",
            rate=snapshot.rate.rate,
            source=snapshot.rate.source,
            fetched_at=snapshot.rate.fetched_at,
            stale=snapshot.rate.stale,
        )
        bank_rate_payload = (
            KimpFxRateResponse(
                pair="USD/KRW",
                rate=bank_fx.rate,
                source=bank_fx.source,
                fetched_at=bank_fx.fetched_at,
                stale=bank_fx.stale,
            )
            if bank_fx is not None
            else None
        )
        errors = list(snapshot.errors)
        if bank_fx_error:
            errors.append(bank_fx_error)
        return KimpScreenerResponse(
            items=items,
            fx=rate_payload,
            bank_fx=bank_rate_payload,
            errors=errors,
            as_of=snapshot.as_of,
        )

    @app.get("/api/kimp-arb/screener", response_model=KimpScreenerResponse)
    async def kimp_screener(
        symbols: str | None = None,
        session: AsyncSession = Depends(_db_session),
        _: AuthenticatedUser = Depends(require_auth),
    ) -> KimpScreenerResponse:
        """심볼별 김프율 + 30일 z-score + 펀딩비 + 시그널 스크리너.

        김프는 Binance USDT-M 무기한 선물 마크가격 기준으로 계산한다.
        ``symbols`` 쿼리는 쉼표 구분 (예: ``BTC,ETH``). 미지정 시 Upbit KRW 현물과
        Binance USDT-M 무기한 선물에 **모두 상장된** 코인 전체를 대상으로 한다.
        """
        return await _build_kimp_screener_response(session, symbols)

    @app.get("/api/kimp-arb/stream", response_class=StreamingResponse)
    async def kimp_screener_stream(
        request: Request,
        symbols: str | None = None,
        interval_sec: float = Query(default=2.0, ge=1.0, le=10.0),
        _: AuthenticatedUser = Depends(require_auth),
    ) -> StreamingResponse:
        """실시간 표시용 김프 스냅샷 스트림. 각 tick은 DB에 저장하지 않는다."""

        async def gen() -> AsyncIterator[str]:
            while not await request.is_disconnected():
                try:
                    async with session_maker() as stream_session:
                        payload = await _build_kimp_screener_response(stream_session, symbols)
                    yield f"data: {payload.model_dump_json()}\n\n"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    err = json.dumps(
                        {
                            "errors": [str(exc)],
                            "as_of": datetime.now(UTC).isoformat(),
                        }
                    )
                    yield f"event: error\ndata: {err}\n\n"
                await asyncio.sleep(interval_sec)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/kimp-arb/history", response_model=KimpHistoryResponse)
    async def kimp_history(
        symbol: str,
        range: Literal["1H", "1D", "7D", "30D", "ALL"] = "1D",
        rate_mode: Literal["usdt", "bank"] = "usdt",
        session: AsyncSession = Depends(_db_session),
        _: AuthenticatedUser = Depends(require_auth),
    ) -> KimpHistoryResponse:
        """심볼별 김프 시계열.

        외부 캔들(Upbit KRW-코인, Binance 코인USDT)과 선택 환율 기준
        (Upbit USDT/KRW 또는 은행 USD/KRW)을 조합해 선택 심볼 1개만 계산하고,
        기간별 결과는 서버 캐시에 저장한다.
        """
        from live.kimp_candle_history import get_kimp_candle_history

        _ = session
        sym = symbol.strip().upper()
        if not sym:
            raise HTTPException(status_code=400, detail="symbol is required")

        history = await get_kimp_candle_history(sym, range, rate_mode=rate_mode)
        points = [KimpHistoryPoint(t=int(ts), p=float(p)) for ts, p in history.series]

        # 김프 트렌드와 펀딩비 트렌드를 같이 보기 위한 펀딩 시계열(좌측 Y축 오버레이).
        # Binance USDT-M 무기한 ``{SYM}USDT`` 펀딩 이력을 가져와 캔들 윈도우에 맞춘다.
        funding_series: list[KimpFundingPoint] = []
        try:
            funding_days = _FUNDING_DAYS_BY_RANGE.get(range, 366)
            funding_rows = await _fetch_funding_history(f"{sym}USDT", days=funding_days)
            if funding_rows:
                start_ms = points[0].t if points else 0
                prev: tuple[int, float] | None = None
                in_window: list[tuple[int, float]] = []
                for ts, rate in funding_rows:
                    if ts < start_ms:
                        prev = (ts, rate)
                        continue
                    in_window.append((ts, rate))
                # 윈도우 좌측 끝에서도 라인이 그려지도록 직전 정산 1건을 포함한다.
                if prev is not None:
                    in_window.insert(0, prev)
                funding_series = [
                    KimpFundingPoint(t=int(ts), r=float(rate) * 100.0)
                    for ts, rate in in_window
                ]
        except Exception:  # noqa: BLE001
            _log.warning("kimp funding history fetch failed symbol=%s", sym, exc_info=True)

        return KimpHistoryResponse(
            symbol=sym,
            range=range,
            rate_mode=history.rate_mode,
            as_of=history.as_of,
            mean_pct=history.mean_pct,
            std_pct=history.std_pct,
            n_samples=history.n_samples,
            series=points,
            funding_series=funding_series,
        )

    # ── Kimchi Premium Delta-Neutral Arbitrage (실거래/백테스트) ──

    @app.get("/api/kimp-arb/status", response_model=KimpArbitrageStatusResponse)
    async def kimp_arb_status(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> KimpArbitrageStatusResponse:
        from live.kimp_neutral_engine import get_engine_status_persisted

        return await get_engine_status_persisted(session, user.user_id)

    @app.post("/api/kimp-arb/start", response_model=KimpArbitrageStatusResponse)
    async def kimp_arb_start(
        params: KimpArbitrageParams,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> KimpArbitrageStatusResponse:
        """김프 델타-중립 봇 시작 (업비트 현물 롱 + 바이낸스 무기한 숏)."""
        from common.crypto import get_crypto_service
        from control.repo import get_binance_credential, get_user_profile
        from live.kimp_neutral_engine import get_engine_status_persisted, start_engine

        current = await get_engine_status_persisted(session, user.user_id)
        if current.running:
            raise HTTPException(
                status_code=409,
                detail=f"이미 실행 중입니다 (symbol={current.symbol}). 먼저 정지하세요.",
            )

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile or not profile.upbit_api_key_enc or not profile.upbit_api_secret_enc:
            raise HTTPException(status_code=400, detail="Upbit API 키가 설정되지 않았습니다.")

        env = params.env
        cred = await get_binance_credential(session, user_id=user.user_id, env=env)
        if not cred:
            raise HTTPException(
                status_code=400, detail=f"Binance {env} API 키가 설정되지 않았습니다."
            )

        crypto = get_crypto_service()
        await start_engine(
            user_id=user.user_id,
            params=params,
            upbit_access=crypto.decrypt(profile.upbit_api_key_enc),
            upbit_secret=crypto.decrypt(profile.upbit_api_secret_enc),
            binance_key=crypto.decrypt(cred.api_key_enc),
            binance_secret=crypto.decrypt(cred.api_secret_enc),
            is_testnet=(env == "testnet"),
            session_maker=session_maker,
        )
        return await get_engine_status_persisted(session, user.user_id)

    @app.post("/api/kimp-arb/stop", response_model=KimpArbitrageStatusResponse)
    async def kimp_arb_stop(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> KimpArbitrageStatusResponse:
        """김프 델타-중립 봇 정지 (어느 replica에서 실행 중이든 정지·청산)."""
        from live.kimp_neutral_engine import get_engine_status_persisted, stop_engine

        await stop_engine(user.user_id, session_maker=session_maker)
        return await get_engine_status_persisted(session, user.user_id)

    @app.post("/api/kimp-arb/backtest", response_model=KimpBacktestResponse)
    async def kimp_arb_backtest(
        req: KimpBacktestRequest,
        session: AsyncSession = Depends(_db_session),
        _: AuthenticatedUser = Depends(require_auth),
    ) -> KimpBacktestResponse:
        """김프 중립 전략 백테스트.

        ``price_source="candles"`` (기본): 업비트 KRW 현물 캔들 + 바이낸스
        USDT-M 선물 캔들로 시세를 구성하고 펀딩 정산 이력을 반영한다(정확).
        ``price_source="snapshots"``: 저장된 ``kimp_snapshots`` 시계열을 사용한다.
        """
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from sqlalchemy import select as _select

        from control.models import KimpSnapshot
        from live.kimp_backtest_data import load_backtest_bars
        from live.kimp_neutral import HedgeMode
        from live.kimp_neutral_backtest import BacktestConfig, KimpBar, run_kimp_backtest

        sym = req.symbol.strip().upper()
        now = _dt.now(UTC)
        if not sym:
            return KimpBacktestResponse(
                success=False, error="symbol is required", symbol=sym, as_of=now
            )

        if req.price_source == "candles":
            try:
                data = await load_backtest_bars(
                    sym,
                    days=req.days,
                    rate_mode=req.rate_mode,
                    include_funding=req.include_funding,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("kimp backtest candle load failed symbol=%s", sym, exc_info=True)
                return KimpBacktestResponse(
                    success=False,
                    error=f"캔들 데이터 조회 실패: {exc}",
                    symbol=sym,
                    as_of=now,
                )
            bars = data.bars
        else:
            since = now - _td(days=req.days)
            stmt = (
                _select(KimpSnapshot)
                .where(KimpSnapshot.symbol == sym, KimpSnapshot.ts >= since)
                .order_by(KimpSnapshot.ts.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            bars = [
                KimpBar(
                    ts_ms=int(r.ts.timestamp() * 1000),
                    upbit_krw=float(r.upbit_krw_price),
                    binance_usdt=float(r.binance_usdt_price),
                    usd_krw=float(r.usd_krw_rate),
                )
                for r in rows
            ]

        if len(bars) < req.z_window_points:
            return KimpBacktestResponse(
                success=False,
                error=(
                    f"데이터 부족: {len(bars)}개 < z_window {req.z_window_points}. "
                    "기간(days)을 늘리거나 윈도우를 줄이세요."
                ),
                symbol=sym,
                as_of=now,
            )

        cfg = BacktestConfig(
            gross_cap_krw=req.gross_cap_krw,
            full_build_z=req.full_build_z,
            flat_z=req.flat_z,
            hedge_mode=HedgeMode.DELTA if req.hedge_mode == "delta" else HedgeMode.QUANTITY,
            leverage=req.leverage,
            z_window=req.z_window_points,
            upbit_taker_fee=req.upbit_taker_fee,
            binance_taker_fee=req.binance_taker_fee,
        )
        result = run_kimp_backtest(bars, cfg)
        m = result.metrics

        equity = result.equity
        max_points = 2000
        step = max(1, len(equity) // max_points)
        curve = [
            KimpBacktestEquityPoint(
                t=p.ts_ms,
                equity_krw=p.equity_krw,
                kimp_pct=p.kimp * 100.0,
                zscore=p.zscore,
                notional_krw=p.notional_krw,
            )
            for p in equity[::step]
        ]
        metrics = KimpBacktestMetrics(
            n_bars=m.n_bars,
            total_return_pct=m.total_return_pct,
            net_profit_krw=m.net_profit_krw,
            funding_income_krw=m.funding_income_krw,
            max_drawdown_pct=m.max_drawdown_pct,
            sharpe=m.sharpe,
            n_rebalances=m.n_rebalances,
            fee_drag_krw=m.fee_drag_krw,
            avg_kimp_pct=m.avg_kimp_pct,
            time_in_market_pct=m.time_in_market_pct,
            final_kimp_pct=m.final_kimp_pct,
        )
        return KimpBacktestResponse(
            success=True, symbol=sym, as_of=now, metrics=metrics, equity_curve=curve
        )

    @app.get(
        "/api/binance/futures/symbols",
        response_model=list[str],
        dependencies=[Depends(require_auth)],
    )
    async def list_binance_futures_symbols() -> list[str]:
        now = time.monotonic()
        cached_symbols = futures_symbols_cache.get("symbols", [])
        if now < float(futures_symbols_cache.get("expires_at", 0.0)) and isinstance(
            cached_symbols, list
        ):
            return [str(item) for item in cached_symbols if isinstance(item, str)]

        from binance.client import normalize_binance_base_url

        base_url = normalize_binance_base_url(
            settings.binance.base_url_backtest
            or settings.binance.base_url
            or "https://fapi.binance.com"
        )

        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                response = await client.get("/fapi/v1/exchangeInfo")
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            if isinstance(cached_symbols, list) and cached_symbols:
                return [str(item) for item in cached_symbols if isinstance(item, str)]
            raise HTTPException(
                status_code=502, detail=f"Failed to fetch Binance futures symbols: {exc}"
            ) from exc

        symbols: list[str] = []
        for raw in payload.get("symbols", []) if isinstance(payload, dict) else []:
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("symbol") or "").strip().upper()
            status = str(raw.get("status") or "").strip().upper()
            contract_type = str(raw.get("contractType") or "").strip().upper()
            quote_asset = str(raw.get("quoteAsset") or "").strip().upper()
            if not symbol or status != "TRADING":
                continue
            if contract_type != "PERPETUAL":
                continue
            if quote_asset != "USDT":
                continue
            symbols.append(symbol)

        symbols = sorted(set(symbols))
        futures_symbols_cache["symbols"] = symbols
        futures_symbols_cache["expires_at"] = now + 900.0
        return symbols

    @app.get(
        "/api/strategies", response_model=list[StrategyInfo], dependencies=[Depends(require_auth)]
    )
    async def strategies(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[StrategyInfo]:
        return await _list_strategies_for_user(session=session, user=user)

    @app.get(
        "/api/strategies/content",
        response_model=StrategyContentResponse,
        dependencies=[Depends(require_auth)],
    )
    async def strategy_content(
        path: str = Query(..., alias="path"),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> StrategyContentResponse:
        name, code = await _resolve_strategy_code_for_user(session=session, user=user, path=path)
        return StrategyContentResponse(name=name, path=_logical_strategy_path(name), code=code)

    @app.delete(
        "/api/strategies",
        response_model=DeleteResponse,
        dependencies=[Depends(require_auth)],
    )
    async def delete_strategy(
        path: str = Query(..., alias="path"),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> DeleteResponse:
        ok = await _delete_strategy_for_user(session=session, user=user, path=path)
        await session.commit()
        return DeleteResponse(ok=ok)

    @app.post(
        "/api/strategies/intake",
        response_model=StrategyIntakeResponse,
        dependencies=[Depends(require_auth)],
    )
    async def intake_strategy(body: StrategyIntakeRequest) -> StrategyIntakeResponse:
        prompt = (body.user_prompt or "").strip()
        messages = body.messages
        if not messages and not prompt:
            raise HTTPException(status_code=422, detail="user_prompt must be non-empty")
        request_id = uuid.uuid4()
        started_at = time.perf_counter()
        message_count = len(messages or [])

        try:
            client = LLMClient()
        except ValueError as exc:
            await _record_strategy_quality(
                request_id=request_id,
                endpoint="intake",
                user_prompt_len=len(prompt),
                message_count=message_count,
                generation_attempted=False,
                generation_success=False,
                error_stage="client_init",
                error_message=str(exc),
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        openai_messages = (
            [{"role": m.role, "content": m.content} for m in messages] if messages else None
        )
        intake = await _run_intake(client, prompt, openai_messages)
        await _record_strategy_quality(
            request_id=request_id,
            endpoint="intake",
            user_prompt_len=len(prompt),
            message_count=message_count,
            intake=intake,
            generation_attempted=False,
            generation_success=False,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
        return intake

    @app.post(
        "/api/llm-test",
        response_model=LlmTestResponse,
        dependencies=[Depends(require_admin)],
    )
    async def llm_test(body: LlmTestRequest) -> LlmTestResponse:
        try:
            client = LLMClient()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        output, error = await client.test_llm(body.input)
        if error:
            raise HTTPException(status_code=502, detail=error)
        if not output:
            raise HTTPException(status_code=502, detail="Empty response from LLM")
        return LlmTestResponse(output=output)

    @app.get(
        "/api/strategies/capabilities",
        response_model=StrategyCapabilityResponse,
        dependencies=[Depends(require_admin)],
    )
    async def strategy_capabilities() -> StrategyCapabilityResponse:
        payload: dict[str, Any] | None = None
        try:
            client = LLMClient()
            payload = await client.strategy_capabilities()
        except ValueError:
            payload = None
        if not payload:
            payload = _local_capability_payload()

        def _to_str_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            out: list[str] = []
            for item in value:
                s = str(item).strip()
                if s:
                    out.append(s)
            return out

        return StrategyCapabilityResponse(
            supported_data_sources=_to_str_list(payload.get("supported_data_sources")),
            supported_indicator_scopes=_to_str_list(payload.get("supported_indicator_scopes")),
            supported_context_methods=_to_str_list(payload.get("supported_context_methods")),
            unsupported_categories=_to_str_list(payload.get("unsupported_categories")),
            summary_lines=_to_str_list(payload.get("summary_lines")),
        )

    @app.get(
        "/api/strategies/quality/summary",
        response_model=StrategyQualitySummaryResponse,
        dependencies=[Depends(require_admin)],
    )
    async def strategy_quality_summary(
        days: int = Query(default=7, ge=1, le=90),
        limit: int = Query(default=5000, ge=100, le=20000),
        session: AsyncSession = Depends(_db_session),
    ) -> StrategyQualitySummaryResponse:
        since = datetime.now(UTC) - timedelta(days=days)
        rows = await list_strategy_quality_logs(session, since=since, limit=limit)

        total_requests = len(rows)
        intake_only_requests = sum(1 for r in rows if r.endpoint == "intake")
        generate_rows = [r for r in rows if r.endpoint in {"generate", "generate_stream"}]
        generate_requests = len(generate_rows)
        generation_success_count = sum(1 for r in generate_rows if r.generation_success is True)
        generation_failure_count = sum(
            1
            for r in generate_rows
            if r.generation_attempted is True and r.generation_success is False
        )
        repaired_count = sum(1 for r in generate_rows if r.repaired is True)
        total_repair_attempts = sum(int(r.repair_attempts or 0) for r in generate_rows)

        ready_count = sum(1 for r in rows if str(r.status or "").upper() == "READY")
        clarification_count = sum(
            1 for r in rows if str(r.status or "").upper() == "NEEDS_CLARIFICATION"
        )
        unsupported_count = sum(
            1 for r in rows if str(r.status or "").upper() == "UNSUPPORTED_CAPABILITY"
        )
        out_of_scope_count = sum(1 for r in rows if str(r.status or "").upper() == "OUT_OF_SCOPE")

        missing_counter: Counter[str] = Counter()
        unsupported_req_counter: Counter[str] = Counter()
        error_stage_counter: Counter[str] = Counter()

        for row in rows:
            for item in row.missing_fields or []:
                key = str(item).strip()
                if key:
                    missing_counter[key] += 1
            for item in row.unsupported_requirements or []:
                key = str(item).strip()
                if key:
                    unsupported_req_counter[key] += 1
            if row.error_stage:
                key = str(row.error_stage).strip()
                if key:
                    error_stage_counter[key] += 1

        return StrategyQualitySummaryResponse(
            window_days=days,
            total_requests=total_requests,
            intake_only_requests=intake_only_requests,
            generate_requests=generate_requests,
            generation_success_count=generation_success_count,
            generation_failure_count=generation_failure_count,
            ready_rate=_rate(ready_count, total_requests),
            clarification_rate=_rate(clarification_count, total_requests),
            unsupported_rate=_rate(unsupported_count, total_requests),
            out_of_scope_rate=_rate(out_of_scope_count, total_requests),
            generation_success_rate=_rate(generation_success_count, generate_requests),
            auto_repair_rate=_rate(repaired_count, generate_requests),
            avg_repair_attempts=round(total_repair_attempts / generate_requests, 4)
            if generate_requests
            else 0.0,
            top_missing_fields=_top_counts(missing_counter, limit=5),
            top_unsupported_requirements=_top_counts(unsupported_req_counter, limit=5),
            top_error_stages=_top_counts(error_stage_counter, limit=5),
        )

    # ── Admin: User management ──────────────────────────────────────

    @app.get(
        "/api/admin/users",
        response_model=AdminUsersResponse,
        dependencies=[Depends(require_admin)],
    )
    async def admin_list_users(
        session: AsyncSession = Depends(_db_session),
    ) -> AdminUsersResponse:
        from control.models import UserProfile

        result = await session.execute(select(UserProfile).order_by(UserProfile.created_at.desc()))
        rows = result.scalars().all()
        items = [
            AdminUserItem(
                user_id=r.user_id,
                email=r.email,
                display_name=r.display_name,
                plan=r.plan,
                email_verified=r.email_verified,
                created_at=r.created_at,
            )
            for r in rows
        ]
        return AdminUsersResponse(users=items, total=len(items))

    @app.delete(
        "/api/admin/users/{user_id:path}",
        dependencies=[Depends(require_admin)],
    )
    async def admin_delete_user(
        user_id: str,
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        from control.models import UserProfile

        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id).limit(1)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=404, detail="User not found")
        await session.delete(profile)
        await session.commit()
        return {"deleted": True, "user_id": user_id}

    @app.post(
        "/api/strategies/generate",
        response_model=StrategyGenerateResponse,
        dependencies=[Depends(require_auth)],
    )
    async def generate_strategy(body: StrategyGenerateRequest) -> StrategyGenerateResponse:
        prompt = (body.user_prompt or "").strip()
        messages = body.messages
        if not messages and not prompt:
            raise HTTPException(status_code=422, detail="user_prompt must be non-empty")
        request_id = uuid.uuid4()
        started_at = time.perf_counter()
        message_count = len(messages or [])
        quality_logged = False

        try:
            try:
                client = LLMClient()
            except ValueError as exc:
                await _record_strategy_quality(
                    request_id=request_id,
                    endpoint="generate",
                    user_prompt_len=len(prompt),
                    message_count=message_count,
                    generation_attempted=False,
                    generation_success=False,
                    error_stage="client_init",
                    error_message=str(exc),
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                )
                quality_logged = True
                raise HTTPException(status_code=503, detail=str(exc)) from exc

            openai_messages = (
                [{"role": m.role, "content": m.content} for m in messages] if messages else None
            )
            if messages:
                result = await client.generate_strategy("", messages=openai_messages)
            else:
                result = await client.generate_strategy(prompt)
            if not result.success or not result.code:
                await _record_strategy_quality(
                    request_id=request_id,
                    endpoint="generate",
                    user_prompt_len=len(prompt),
                    message_count=message_count,
                    generation_attempted=True,
                    generation_success=False,
                    verification_passed=False,
                    error_stage="model_generation",
                    error_message=result.error or "LLM generation failed",
                    model_used=result.model_used,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                )
                quality_logged = True
                raise HTTPException(status_code=502, detail=result.error or "LLM generation failed")

            code = _strip_code_fences(result.code)
            if not code:
                await _record_strategy_quality(
                    request_id=request_id,
                    endpoint="generate",
                    user_prompt_len=len(prompt),
                    message_count=message_count,
                    generation_attempted=True,
                    generation_success=False,
                    verification_passed=False,
                    error_stage="empty_code",
                    error_message="LLM returned empty code",
                    model_used=result.model_used,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                )
                quality_logged = True
                raise HTTPException(status_code=502, detail="LLM returned empty code")

            response = StrategyGenerateResponse(
                path=None,
                code=code,
                model_used=result.model_used,
                summary=None,
                backtest_ok=False,
                repaired=False,
                repair_attempts=0,
            )
            await _record_strategy_quality(
                request_id=request_id,
                endpoint="generate",
                user_prompt_len=len(prompt),
                message_count=message_count,
                generation_attempted=True,
                generation_success=True,
                verification_passed=True,
                repaired=False,
                repair_attempts=0,
                model_used=result.model_used,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
            quality_logged = True
            return response
        except HTTPException:
            raise
        except Exception as exc:
            if not quality_logged:
                await _record_strategy_quality(
                    request_id=request_id,
                    endpoint="generate",
                    user_prompt_len=len(prompt),
                    message_count=message_count,
                    generation_attempted=True,
                    generation_success=False,
                    verification_passed=False,
                    error_stage="unhandled",
                    error_message=str(exc),
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                )
            raise

    async def _generate_stream_events(body: StrategyGenerateRequest):
        prompt = (body.user_prompt or "").strip()
        messages = body.messages
        request_id = uuid.uuid4()
        started_at = time.perf_counter()
        message_count = len(messages or [])
        quality_logged = False

        async def _log_once(
            *,
            generation_attempted: bool | None,
            generation_success: bool | None,
            verification_passed: bool | None = None,
            repaired: bool | None = None,
            repair_attempts: int = 0,
            error_stage: str | None = None,
            error_message: str | None = None,
            model_used: str | None = None,
        ) -> None:
            nonlocal quality_logged
            if quality_logged:
                return
            await _record_strategy_quality(
                request_id=request_id,
                endpoint="generate_stream",
                user_prompt_len=len(prompt),
                message_count=message_count,
                generation_attempted=generation_attempted,
                generation_success=generation_success,
                verification_passed=verification_passed,
                repaired=repaired,
                repair_attempts=repair_attempts,
                model_used=model_used,
                error_stage=error_stage,
                error_message=error_message,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
            quality_logged = True

        if not messages and not prompt:
            await _log_once(
                generation_attempted=False,
                generation_success=False,
                error_stage="invalid_input",
                error_message="user_prompt must be non-empty",
            )
            yield f"data: {json.dumps({'error': 'user_prompt must be non-empty'})}\n\n"
            return
        try:
            client = LLMClient()
        except ValueError as exc:
            await _log_once(
                generation_attempted=False,
                generation_success=False,
                error_stage="client_init",
                error_message=str(exc),
            )
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            return

        openai_messages = (
            [{"role": m.role, "content": m.content} for m in messages] if messages else None
        )

        code_acc: list[str] = []
        stream_repaired = False
        stream_repair_attempts = 0
        try:
            if messages:
                stream = client.generate_strategy_stream(
                    "", messages=openai_messages, confirmed_plan=body.confirmed_plan
                )
            else:
                stream = client.generate_strategy_stream(prompt, confirmed_plan=body.confirmed_plan)
            async for event in stream:
                if "error" in event:
                    await _log_once(
                        generation_attempted=True,
                        generation_success=False,
                        verification_passed=False,
                        error_stage="stream_generation",
                        error_message=str(event.get("error")),
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                    return
                # Forward phase events from llm to frontend
                if "phase" in event:
                    yield f"data: {json.dumps(event)}\n\n"
                # Forward intent routing events (e.g. question) to frontend
                if "intent" in event:
                    yield f"data: {json.dumps(event)}\n\n"
                    return
                # Forward plan_preview events to frontend
                if "plan_preview" in event:
                    yield f"data: {json.dumps(event)}\n\n"
                    return
                if "token" in event:
                    code_acc.append(event["token"])
                    yield f"data: {json.dumps({'token': event['token']})}\n\n"
                if event.get("done"):
                    # Relay already did verify+repair; extract results
                    stream_repaired = event.get("repaired", False)
                    stream_repair_attempts = event.get("repair_attempts", 0)
                    if event.get("rejected"):
                        # Non-trading request rejected by planner
                        rejection_msg = event.get("code", "")
                        await _log_once(
                            generation_attempted=True,
                            generation_success=False,
                            verification_passed=False,
                            error_stage="planner_rejected",
                            error_message="Non-trading request",
                        )
                        yield f"data: {json.dumps({'done': True, 'rejected': True, 'code': rejection_msg, 'repaired': False, 'repair_attempts': 0})}\n\n"
                        return
                    if event.get("code"):
                        code_acc = [event["code"]]
                    break
        except Exception as exc:  # noqa: BLE001
            await _log_once(
                generation_attempted=True,
                generation_success=False,
                verification_passed=False,
                error_stage="stream_exception",
                error_message=str(exc),
            )
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            return
        code = _strip_code_fences("".join(code_acc))
        if not code:
            await _log_once(
                generation_attempted=True,
                generation_success=False,
                verification_passed=False,
                error_stage="empty_code",
                error_message="Empty code from stream",
            )
            yield f"data: {json.dumps({'error': 'Empty code from stream'})}\n\n"
            return
        await _log_once(
            generation_attempted=True,
            generation_success=True,
            verification_passed=True,
            repaired=stream_repaired,
            repair_attempts=stream_repair_attempts,
        )
        yield (
            "data: "
            + json.dumps(
                {
                    "done": True,
                    "code": code,
                    "summary": None,
                    "backtest_ok": False,
                    "repaired": stream_repaired,
                    "repair_attempts": stream_repair_attempts,
                }
            )
            + "\n\n"
        )

    @app.post("/api/strategies/generate/stream")
    async def generate_strategy_stream_endpoint(body: StrategyGenerateRequest):
        return StreamingResponse(
            _generate_stream_events(body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/strategies/chat",
        response_model=StrategyChatResponse,
        dependencies=[Depends(require_auth)],
    )
    async def strategy_chat(body: StrategyChatRequest) -> StrategyChatResponse:
        code = (body.code or "").strip()
        if not body.messages:
            raise HTTPException(status_code=422, detail="messages must be non-empty")
        try:
            client = LLMClient()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        openai_messages = [{"role": m.role, "content": m.content} for m in body.messages]
        content = await client.strategy_chat(code, body.summary, openai_messages)
        if content is None:
            raise HTTPException(status_code=502, detail="Strategy chat failed")
        return StrategyChatResponse(content=content)

    async def _strategy_chat_stream_events(body: StrategyChatRequest):
        code = (body.code or "").strip()
        if not body.messages:
            yield f"data: {json.dumps({'error': 'messages must be non-empty'})}\n\n"
            return
        try:
            client = LLMClient()
        except ValueError as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            return
        openai_messages = [{"role": m.role, "content": m.content} for m in body.messages]
        try:
            async for event in client.strategy_chat_stream(code, body.summary, openai_messages):
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("done") or event.get("error"):
                    return
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    @app.post("/api/strategies/chat/stream")
    async def strategy_chat_stream_endpoint(body: StrategyChatRequest):
        return StreamingResponse(
            _strategy_chat_stream_events(body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/strategies/analyze")
    async def analyze_strategy_endpoint(body: dict):
        """Analyze backtest results with the analyst model."""
        code = (body.get("code") or "").strip()
        backtest_results = (body.get("backtest_results") or "").strip()
        summary = body.get("summary")
        if not code or not backtest_results:
            raise HTTPException(status_code=422, detail="code and backtest_results required")
        try:
            client = LLMClient()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        analysis = await client.analyze_backtest(code, backtest_results, summary)
        if analysis is None:
            raise HTTPException(status_code=502, detail="Analysis failed")
        return {"analysis": analysis}

    @app.get(
        "/api/strategies/chat/sessions",
        response_model=list[StrategyChatSessionResponse],
        dependencies=[Depends(require_auth)],
    )
    async def list_chat_sessions(
        limit: int = Query(default=200, ge=1, le=500),
        user_id: str = Depends(_chat_user_id_from_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[StrategyChatSessionResponse]:
        rows = await repo_list_strategy_chat_sessions(session, user_id=user_id, limit=limit)
        return [_chat_session_to_response(row) for row in rows]

    @app.get(
        "/api/strategies/chat/sessions/list",
        response_model=list[StrategyChatSessionSummary],
        dependencies=[Depends(require_auth)],
    )
    async def list_chat_session_summaries(
        limit: int = Query(default=200, ge=1, le=500),
        user_id: str = Depends(_chat_user_id_from_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[StrategyChatSessionSummary]:
        """Lightweight session list — metadata only, no data payload."""
        rows = await repo_list_strategy_chat_session_summaries(
            session, user_id=user_id, limit=limit
        )
        return [_chat_session_to_summary(row) for row in rows]

    @app.get(
        "/api/strategies/chat/sessions/{session_id}",
        response_model=StrategyChatSessionResponse,
        dependencies=[Depends(require_auth)],
    )
    async def get_chat_session(
        session_id: str,
        user_id: str = Depends(_chat_user_id_from_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> StrategyChatSessionResponse:
        row = await repo_get_strategy_chat_session(session, user_id=user_id, session_id=session_id)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        return _chat_session_to_response(row)

    @app.put(
        "/api/strategies/chat/sessions/{session_id}",
        response_model=StrategyChatSessionResponse,
        dependencies=[Depends(require_auth)],
    )
    async def upsert_chat_session(
        session_id: str,
        body: StrategyChatSessionUpsertRequest,
        user_id: str = Depends(_chat_user_id_from_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> StrategyChatSessionResponse:
        normalized_session_id = (session_id or "").strip()
        if not normalized_session_id:
            raise HTTPException(status_code=422, detail="session_id must be non-empty")
        if len(normalized_session_id) > 128:
            raise HTTPException(status_code=422, detail="session_id too long")

        title = (body.title or "").strip() or "New chat"
        if len(title) > 200:
            title = title[:200]

        row = await repo_upsert_strategy_chat_session(
            session,
            user_id=user_id,
            session_id=normalized_session_id,
            title=title,
            data_json=body.data,
        )
        await session.commit()
        return _chat_session_to_response(row)

    @app.delete(
        "/api/strategies/chat/sessions/{session_id}",
        response_model=DeleteResponse,
        dependencies=[Depends(require_auth)],
    )
    async def delete_chat_session(
        session_id: str,
        user_id: str = Depends(_chat_user_id_from_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> DeleteResponse:
        normalized_session_id = (session_id or "").strip()
        if not normalized_session_id:
            raise HTTPException(status_code=422, detail="session_id must be non-empty")
        ok = await repo_delete_strategy_chat_session(
            session,
            user_id=user_id,
            session_id=normalized_session_id,
        )
        await session.commit()
        return DeleteResponse(ok=ok)

    @app.post(
        "/api/strategies/save",
        response_model=StrategySaveResponse,
        dependencies=[Depends(require_auth)],
    )
    async def save_strategy(
        body: StrategySaveRequest,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> StrategySaveResponse:
        code = (body.code or "").strip()
        if not code:
            raise HTTPException(status_code=422, detail="code must be non-empty")

        code = _strip_code_fences(code)
        filename = _sanitize_strategy_filename(body.strategy_name)
        repo_root = _repo_root()
        temp_path = _verify_tmp_dir() / f"_verify_{uuid.uuid4().hex}_strategy.py"

        try:
            temp_path.write_text(code, encoding="utf-8")
            _verify_strategy_load(temp_path, repo_root)
            _verify_strategy_backtest(temp_path, repo_root)
        except ValueError as exc:
            _cleanup_verify_temp(temp_path)
            raise HTTPException(
                status_code=502, detail=f"Strategy verification failed: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            _cleanup_verify_temp(temp_path)
            raise HTTPException(
                status_code=502, detail=f"Strategy verification failed: {exc}"
            ) from exc

        _cleanup_verify_temp(temp_path)

        storage = get_strategy_storage()
        if storage is not None:
            try:
                blob_path = storage.upload(user.user_id, filename, code)
                await upsert_strategy_meta(
                    session,
                    user_id=user.user_id,
                    strategy_name=filename,
                    blob_path=blob_path,
                )
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=500, detail=f"Failed to upload strategy object: {exc}"
                ) from exc
            return StrategySaveResponse(path=_logical_strategy_path(filename))

        dirs = _strategy_dirs()
        if not dirs:
            raise HTTPException(status_code=500, detail="STRATEGY_DIRS is not configured")

        base_dir = dirs[0]
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"Failed to prepare strategy dir: {exc}"
            ) from exc

        final_target = _unique_strategy_path(base_dir, filename)
        try:
            final_target.write_text(code, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"Failed to write strategy file: {exc}"
            ) from exc
        return StrategySaveResponse(path=str(final_target.relative_to(repo_root)))

    @app.post(
        "/api/strategies/validate-syntax",
        response_model=StrategySyntaxCheckResponse,
        dependencies=[Depends(require_auth)],
    )
    async def validate_strategy_syntax(
        body: StrategySyntaxCheckRequest,
    ) -> StrategySyntaxCheckResponse:
        code = _strip_code_fences(body.code or "")
        if not code.strip():
            raise HTTPException(status_code=422, detail="code must be non-empty")
        try:
            ast.parse(code)
            return StrategySyntaxCheckResponse(valid=True, error=None)
        except SyntaxError as exc:
            return StrategySyntaxCheckResponse(
                valid=False,
                error=StrategySyntaxError(
                    message=exc.msg or "invalid syntax",
                    line=exc.lineno,
                    column=(exc.offset - 1)
                    if isinstance(exc.offset, int) and exc.offset > 0
                    else exc.offset,
                    end_line=getattr(exc, "end_lineno", None),
                    end_column=(
                        (exc.end_offset - 1)
                        if isinstance(getattr(exc, "end_offset", None), int)
                        and getattr(exc, "end_offset", None) > 0
                        else getattr(exc, "end_offset", None)
                    ),
                ),
            )

    @app.post(
        "/api/strategies/params/extract",
        response_model=StrategyParamsExtractResponse,
        dependencies=[Depends(require_auth)],
    )
    async def extract_strategy_params_endpoint(
        body: StrategyParamsExtractRequest,
    ) -> StrategyParamsExtractResponse:
        code = _strip_code_fences(body.code or "")
        if not code.strip():
            return StrategyParamsExtractResponse(supported=False, values={}, schema_fields={})
        try:
            values, schema_fields, supported = extract_strategy_params(code)
            return StrategyParamsExtractResponse(
                supported=supported,
                values=values,
                schema_fields=schema_fields,
            )
        except StrategyParamsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/strategies/params/apply",
        response_model=StrategyParamsApplyResponse,
        dependencies=[Depends(require_auth)],
    )
    async def apply_strategy_params_endpoint(
        body: StrategyParamsApplyRequest,
    ) -> StrategyParamsApplyResponse:
        code = _strip_code_fences(body.code or "")
        if not code.strip():
            raise HTTPException(status_code=422, detail="code must be non-empty")
        try:
            new_code = apply_strategy_params(code, dict(body.param_values or {}))
            return StrategyParamsApplyResponse(code=new_code)
        except StrategyParamsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/strategies/backtest/quick",
        response_model=QuickBacktestResponse,
    )
    async def quick_backtest_endpoint(
        body: QuickBacktestRequest,
        user: AuthenticatedUser = Depends(require_auth),
    ) -> QuickBacktestResponse:
        from api.quick_backtest import run_quick_backtest

        return await run_quick_backtest(body, user_id=user.user_id, plan=user.plan)

    @app.post(
        "/api/jobs/preflight",
        response_model=JobPolicyCheckResponse,
        dependencies=[Depends(require_auth)],
    )
    async def preflight_job_policy(body: JobPolicyCheckRequest) -> JobPolicyCheckResponse:
        result = evaluate_job_policy(body.type, body.config)
        return JobPolicyCheckResponse(
            ok=result.ok, blockers=result.blockers, warnings=result.warnings
        )

    @app.post("/api/jobs", response_model=JobResponse)
    async def create_job_api(
        body: JobCreateRequest,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> JobResponse:
        policy = evaluate_job_policy(body.type, body.config)
        if policy.blockers:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Job policy check failed",
                    "blockers": policy.blockers,
                    "warnings": policy.warnings,
                },
            )

        from api.quota import check_job_quota

        await check_job_quota(session, user_id=user.user_id, plan=user.plan, job_type=body.type)

        wallet_account_id = body.wallet_account_id
        if wallet_account_id is not None:
            if body.type != JobType.LIVE:
                raise HTTPException(
                    status_code=422,
                    detail="wallet_account_id is only supported for LIVE jobs.",
                )
            wallet = await get_wallet_account(session, wallet_account_id=wallet_account_id)
            if wallet is None or wallet.user_id != user.user_id:
                raise HTTPException(status_code=404, detail="Wallet account not found")
            env = str(body.config.get("env") or "mainnet").strip().lower()
            if wallet.env != env:
                raise HTTPException(
                    status_code=422,
                    detail=f"Wallet env({wallet.env}) does not match job env({env}).",
                )
            status = wallet.status.value if hasattr(wallet.status, "value") else str(wallet.status)
            if status != WalletAccountStatus.ACTIVE.value:
                raise HTTPException(
                    status_code=422,
                    detail=f"Wallet account must be active before live trading (status={status}).",
                )
            if not wallet.api_key_enc or not wallet.api_secret_enc:
                raise HTTPException(
                    status_code=422,
                    detail="Wallet account must have API keys before live trading.",
                )
            role = wallet.role.value if hasattr(wallet.role, "value") else str(wallet.role)
            if role == WalletRole.SUB.value and not bool(
                (wallet.enabled_wallets or {}).get("futures_um")
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Selected sub account does not have USD-M futures enabled.",
                )

        try:
            strategy_name, strategy_code = await _resolve_strategy_code_for_user(
                session=session,
                user=user,
                path=body.strategy_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        config_json = dict(body.config)
        config_json["_strategy_code"] = strategy_code
        job = await create_job(
            session,
            user_id=user.user_id,
            job_type=body.type,
            strategy_path=_logical_strategy_path(strategy_name),
            config_json=config_json,
            wallet_account_id=wallet_account_id,
        )
        await append_event(
            session,
            job_id=job.job_id,
            kind=EventKind.STATUS,
            message="JOB_CREATED",
            payload_json={
                "type": str(body.type),
                "strategy_path": body.strategy_path,
                "wallet_account_id": str(wallet_account_id) if wallet_account_id else None,
            },
        )
        if policy.warnings:
            await append_event(
                session,
                job_id=job.job_id,
                kind=EventKind.RISK,
                message="POLICY_WARNINGS",
                payload_json={"warnings": policy.warnings},
            )
        await session.commit()
        return _job_to_response(job)

    # ------------------------------------------------------------------
    # Backtest Sweep: run many backtests at once (parameter grid) and
    # compare them as a group. A sweep is just a set of BACKTEST jobs that
    # share a ``config_json._sweep.sweep_id`` — no schema/runner changes.
    # ------------------------------------------------------------------

    def _evaluate_sweep_policy(
        expanded: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[list[str], list[str]]:
        blockers: list[str] = []
        warnings_set: dict[str, None] = {}
        for run_index, (varied, cfg) in enumerate(expanded):
            res = evaluate_job_policy(JobType.BACKTEST, cfg)
            label = ", ".join(f"{k}={v}" for k, v in varied.items())
            for blocker in res.blockers:
                if len(blockers) < 50:
                    blockers.append(f"run#{run_index + 1} ({label}): {blocker}")
            for warning in res.warnings:
                warnings_set.setdefault(warning, None)
        return blockers, list(warnings_set.keys())

    @app.post(
        "/api/backtest/sweeps/preflight",
        response_model=SweepPreflightResponse,
        dependencies=[Depends(require_auth)],
    )
    async def preflight_sweep(body: SweepPreflightRequest) -> SweepPreflightResponse:
        from api.backtest_sweep import (
            MAX_SWEEP_TOTAL_RUNS,
            SweepError,
            build_dimensions,
            expand,
        )

        try:
            dims = build_dimensions([d.model_dump() for d in body.dimensions])
        except SweepError as exc:
            return SweepPreflightResponse(
                ok=False,
                total_runs=0,
                max_runs=MAX_SWEEP_TOTAL_RUNS,
                blockers=[str(exc)],
            )

        base = {
            k: v
            for k, v in dict(body.base_config).items()
            if k not in INTERNAL_JOB_CONFIG_KEYS and k != "_sweep"
        }
        expanded = expand(base, dims)
        blockers, warnings = _evaluate_sweep_policy(expanded)
        preview = [
            SweepRunPreview(index=i, params=varied)
            for i, (varied, _) in enumerate(expanded[:50])
        ]
        return SweepPreflightResponse(
            ok=not blockers,
            total_runs=len(expanded),
            max_runs=MAX_SWEEP_TOTAL_RUNS,
            blockers=blockers,
            warnings=warnings,
            dimensions=[
                SweepDimensionResolved(path=d.path, values=list(d.values))
                for d in dims
            ],
            preview=preview,
        )

    @app.post("/api/backtest/sweeps", response_model=SweepCreateResponse)
    async def create_sweep_api(
        body: SweepCreateRequest,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> SweepCreateResponse:
        from api.backtest_sweep import SweepError, build_dimensions, expand

        try:
            dims = build_dimensions([d.model_dump() for d in body.dimensions])
        except SweepError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        base = {
            k: v
            for k, v in dict(body.base_config).items()
            if k not in INTERNAL_JOB_CONFIG_KEYS and k != "_sweep"
        }
        expanded = expand(base, dims)

        blockers, warnings = _evaluate_sweep_policy(expanded)
        if blockers:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Sweep policy check failed",
                    "blockers": blockers,
                    "warnings": warnings,
                },
            )

        from api.quota import check_job_quota

        await check_job_quota(
            session, user_id=user.user_id, plan=user.plan, job_type=JobType.BACKTEST
        )

        try:
            strategy_name, strategy_code = await _resolve_strategy_code_for_user(
                session=session,
                user=user,
                path=body.strategy_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        sweep_id = uuid.uuid4().hex
        logical_path = _logical_strategy_path(strategy_name)
        total = len(expanded)
        spec = {
            "strategy_path": logical_path,
            "base_config": base,
            "dimensions": [
                {"path": d.path, "values": list(d.values)} for d in dims
            ],
        }

        # Cache resolved strategy code per raw path so a strategy_path sweep
        # only resolves each distinct strategy once.
        strategy_cache: dict[str, tuple[str, str, str]] = {
            body.strategy_path: (strategy_name, strategy_code, logical_path)
        }

        async def _resolve_for_run(raw_path: str) -> tuple[str, str, str]:
            cached = strategy_cache.get(raw_path)
            if cached is not None:
                return cached
            try:
                run_name, run_code = await _resolve_strategy_code_for_user(
                    session=session,
                    user=user,
                    path=raw_path,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            resolved = (run_name, run_code, _logical_strategy_path(run_name))
            strategy_cache[raw_path] = resolved
            return resolved

        job_ids: list[uuid.UUID] = []
        for run_index, (varied, cfg) in enumerate(expanded):
            config_json = dict(cfg)
            run_raw_path = config_json.pop("strategy_path", body.strategy_path)
            run_name, run_code, run_logical = await _resolve_for_run(run_raw_path)
            config_json["_strategy_code"] = run_code
            config_json["_sweep"] = {
                "sweep_id": sweep_id,
                "index": run_index,
                "total": total,
                "params": varied,
                "spec": spec,
            }
            job = await create_job(
                session,
                user_id=user.user_id,
                job_type=JobType.BACKTEST,
                strategy_path=run_logical,
                config_json=config_json,
            )
            await append_event(
                session,
                job_id=job.job_id,
                kind=EventKind.STATUS,
                message="JOB_CREATED",
                payload_json={
                    "type": str(JobType.BACKTEST),
                    "strategy_path": run_raw_path,
                    "sweep_id": sweep_id,
                    "sweep_index": run_index,
                },
            )
            job_ids.append(job.job_id)

        if warnings and job_ids:
            await append_event(
                session,
                job_id=job_ids[0],
                kind=EventKind.RISK,
                message="POLICY_WARNINGS",
                payload_json={"warnings": warnings},
            )

        await session.commit()
        return SweepCreateResponse(
            sweep_id=sweep_id, total_runs=total, job_ids=job_ids
        )

    @app.get("/api/backtest/sweeps", response_model=list[SweepListItem])
    async def list_sweeps_api(
        limit: int = Query(default=50, ge=1, le=200),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[SweepListItem]:
        rows = await list_sweep_group_rows(session, user_id=user.user_id, limit=1000)

        groups: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for row in rows:
            config = row.config_json if isinstance(row.config_json, dict) else {}
            sweep = config.get("_sweep") if isinstance(config.get("_sweep"), dict) else {}
            sweep_id = sweep.get("sweep_id")
            if not sweep_id:
                continue
            if sweep_id not in groups:
                spec = sweep.get("spec") if isinstance(sweep.get("spec"), dict) else {}
                varied_paths = [
                    d.get("path")
                    for d in (spec.get("dimensions") or [])
                    if isinstance(d, dict) and d.get("path")
                ]
                groups[sweep_id] = {
                    "sweep_id": sweep_id,
                    "strategy_path": spec.get("strategy_path") or row.strategy_path,
                    "symbol": config.get("symbol"),
                    "interval": config.get("interval"),
                    "total_runs": int(sweep.get("total") or 0),
                    "completed_runs": 0,
                    "status_counts": {},
                    "varied_paths": varied_paths,
                    "created_at": row.created_at,
                }
                order.append(sweep_id)
            group = groups[sweep_id]
            status_key = str(row.status)
            group["status_counts"][status_key] = (
                group["status_counts"].get(status_key, 0) + 1
            )
            if row.status in (
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.STOPPED,
            ):
                group["completed_runs"] += 1
            if row.created_at and (
                group["created_at"] is None or row.created_at > group["created_at"]
            ):
                group["created_at"] = row.created_at
            # Fallback when sweep.total is missing/0: count children.
            if not group["total_runs"]:
                group["total_runs"] = sum(group["status_counts"].values())

        items = [
            SweepListItem(
                sweep_id=g["sweep_id"],
                strategy_path=g["strategy_path"],
                symbol=g["symbol"],
                interval=g["interval"],
                total_runs=g["total_runs"] or sum(g["status_counts"].values()),
                completed_runs=g["completed_runs"],
                status_counts=g["status_counts"],
                varied_paths=g["varied_paths"],
                created_at=g["created_at"],
            )
            for g in (groups[sid] for sid in order)
        ]
        items.sort(key=lambda it: it.created_at, reverse=True)
        return items[:limit]

    @app.get("/api/backtest/sweeps/{sweep_id}", response_model=SweepDetailResponse)
    async def get_sweep_api(
        sweep_id: str,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> SweepDetailResponse:
        rows = await list_sweep_child_rows(
            session, user_id=user.user_id, sweep_id=sweep_id
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Sweep not found")

        def _sweep_meta(row: Any) -> dict[str, Any]:
            config = row.config_json if isinstance(row.config_json, dict) else {}
            sweep = config.get("_sweep")
            return sweep if isinstance(sweep, dict) else {}

        rows_sorted = sorted(rows, key=lambda r: int(_sweep_meta(r).get("index") or 0))
        spec = _sweep_meta(rows_sorted[0]).get("spec")
        spec = spec if isinstance(spec, dict) else {}

        runs: list[SweepRunResult] = []
        for row in rows_sorted:
            meta = _sweep_meta(row)
            summary = row.result_summary if isinstance(row.result_summary, dict) else None
            runs.append(
                SweepRunResult(
                    job_id=row.job_id,
                    index=int(meta.get("index") or 0),
                    params=meta.get("params") if isinstance(meta.get("params"), dict) else {},
                    status=row.status,
                    error=row.error,
                    result_summary=summary,
                )
            )

        created_candidates = [r.created_at for r in rows_sorted if r.created_at]
        created_at = min(created_candidates) if created_candidates else rows_sorted[0].created_at
        dimensions = [
            SweepDimensionResolved(
                path=d.get("path"),
                values=[float(v) for v in (d.get("values") or [])],
            )
            for d in (spec.get("dimensions") or [])
            if isinstance(d, dict) and d.get("path")
        ]
        runs_strategy_path = spec.get("strategy_path") or ""
        return SweepDetailResponse(
            sweep_id=sweep_id,
            strategy_path=runs_strategy_path,
            base_config=spec.get("base_config") if isinstance(spec.get("base_config"), dict) else {},
            dimensions=dimensions,
            total_runs=len(rows_sorted),
            created_at=created_at,
            runs=runs,
        )

    @app.post(
        "/api/backtest/sweeps/{sweep_id}/stop", response_model=StopAllResponse
    )
    async def stop_sweep_api(
        sweep_id: str,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> StopAllResponse:
        rows = await list_sweep_child_rows(
            session, user_id=user.user_id, sweep_id=sweep_id
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Sweep not found")

        stopped_queued = 0
        stop_requested_running = 0
        for row in rows:
            new_status = await request_stop(session, row.job_id, user_id=user.user_id)
            if new_status == JobStatus.STOPPED:
                stopped_queued += 1
            elif new_status == JobStatus.STOP_REQUESTED:
                stop_requested_running += 1
        await session.commit()
        return StopAllResponse(
            stopped_queued=stopped_queued,
            stop_requested_running=stop_requested_running,
        )

    @app.get("/api/jobs", response_model=list[JobResponse])
    async def jobs(
        limit: int = Query(default=50, ge=1, le=200),
        job_type: JobType | None = Query(default=None, alias="type"),
        status: JobStatus | None = Query(default=None),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[JobResponse]:
        rows = await list_jobs(
            session,
            user_id=user.user_id,
            limit=limit,
            job_type=job_type,
            status=status,
        )
        return [_job_to_response(j) for j in rows]

    @app.get("/api/jobs/list", response_model=list[JobSummary])
    async def jobs_summary(
        limit: int = Query(default=50, ge=1, le=200),
        job_type: JobType | None = Query(default=None, alias="type"),
        status: JobStatus | None = Query(default=None),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[JobSummary]:
        """Lightweight job list — heavy result keys (chart, trades) are stripped at SQL projection time."""
        rows = await list_job_summaries(
            session,
            user_id=user.user_id,
            limit=limit,
            job_type=job_type,
            status=status,
        )
        return [_job_summary_row_to_response(r) for r in rows]

    @app.get("/api/jobs/counts", response_model=JobCountsResponse)
    async def job_counts(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> JobCountsResponse:
        backtest_total = await count_jobs(session, user_id=user.user_id, job_type=JobType.BACKTEST)
        live_total = await count_jobs(session, user_id=user.user_id, job_type=JobType.LIVE)
        return JobCountsResponse(backtest_total=backtest_total, live_total=live_total)

    @app.post("/api/jobs/stop-all", response_model=StopAllResponse)
    async def stop_all(
        job_type: JobType | None = Query(default=None, alias="type"),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> StopAllResponse:
        counts = await stop_all_jobs(session, user_id=user.user_id, job_type=job_type)
        await session.commit()
        return StopAllResponse(**counts)

    @app.delete("/api/jobs", response_model=DeleteAllResponse)
    async def delete_all(
        job_type: JobType | None = Query(default=None, alias="type"),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> DeleteAllResponse:
        counts = await delete_jobs(session, user_id=user.user_id, job_type=job_type)
        await session.commit()
        return DeleteAllResponse(**counts)

    # ------------------------------------------------------------------
    # Batch trades endpoint (must be before {job_id} routes)
    # ------------------------------------------------------------------

    @app.get("/api/jobs/trades/batch")
    async def trades_batch(
        job_ids: str = Query(..., description="Comma-separated job UUIDs"),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, list[TradeResponse]]:
        """Fetch trades for multiple jobs in a single request."""
        raw_ids = [s.strip() for s in job_ids.split(",") if s.strip()]
        if len(raw_ids) > 20:
            raise HTTPException(status_code=400, detail="Maximum 20 job IDs per batch request")
        parsed: list[uuid.UUID] = []
        for raw in raw_ids:
            try:
                parsed.append(uuid.UUID(raw))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid UUID: {raw}")  # noqa: B904
        # Verify all jobs belong to the user
        for jid in parsed:
            job = await get_job(session, jid, user_id=user.user_id)
            if not job:
                raise HTTPException(status_code=404, detail=f"Job not found: {jid}")
        batch = await list_trades_batch(session, job_ids=parsed)
        return {
            str(jid): [
                TradeResponse(
                    trade_id=t.trade_id,
                    symbol=t.symbol,
                    order_id=t.order_id,
                    quantity=t.quantity,
                    price=t.price,
                    realized_pnl=t.realized_pnl,
                    commission=t.commission,
                    ts=t.ts,
                    raw=t.raw_json,
                )
                for t in trades_list
            ]
            for jid, trades_list in batch.items()
        }

    # ------------------------------------------------------------------
    # SSE: Live job trades stream (must be before {job_id} routes)
    # ------------------------------------------------------------------

    @app.get("/api/jobs/live/stream", response_class=StreamingResponse)
    async def live_jobs_stream(
        user: AuthenticatedUser = Depends(require_auth),
    ) -> StreamingResponse:
        """SSE stream that pushes live job summaries + trades periodically."""

        async def gen() -> AsyncIterator[bytes]:
            yield b"retry: 5000\n\n"
            try:
                while True:
                    async with session_maker() as session:
                        running_jobs = await list_jobs(
                            session,
                            user_id=user.user_id,
                            job_type=JobType.LIVE,
                            status=JobStatus.RUNNING,
                            limit=20,
                        )
                        job_ids = [j.job_id for j in running_jobs]
                        trades_map: dict[uuid.UUID, list[Any]] = {}
                        if job_ids:
                            trades_map = await list_trades_batch(session, job_ids=job_ids)
                        payload = {
                            "jobs": [
                                {
                                    "job_id": str(j.job_id),
                                    "status": j.status.value
                                    if hasattr(j.status, "value")
                                    else str(j.status),
                                    "strategy_path": j.strategy_path,
                                    "config": _public_job_config(j.config),
                                    "started_at": j.started_at.isoformat()
                                    if j.started_at
                                    else None,
                                    "trades": [
                                        {
                                            "trade_id": t.trade_id,
                                            "symbol": t.symbol,
                                            "realized_pnl": t.realized_pnl,
                                            "quantity": t.quantity,
                                            "price": t.price,
                                            "commission": t.commission,
                                            "ts": t.ts.isoformat(),
                                        }
                                        for t in trades_map.get(j.job_id, [])
                                    ],
                                }
                                for j in running_jobs
                            ],
                        }
                    data = json.dumps(payload, ensure_ascii=False, default=str)
                    yield f"data: {data}\n\n".encode()
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                return

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    async def job_detail(
        job_id: uuid.UUID,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> JobResponse:
        job = await get_job(session, job_id, user_id=user.user_id)
        if not job:
            raise HTTPException(status_code=404, detail="Not found")
        return _job_to_response(job)

    @app.delete("/api/jobs/{job_id}", response_model=DeleteResponse)
    async def delete_single_job(
        job_id: uuid.UUID,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> DeleteResponse:
        deleted, status = await delete_job(session, job_id, user_id=user.user_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Not found")
        if not deleted:
            raise HTTPException(
                status_code=409,
                detail={"message": "Cannot delete active job", "status": str(status)},
            )
        await session.commit()
        return DeleteResponse(ok=True)

    @app.post("/api/jobs/{job_id}/stop", response_model=StopResponse)
    async def stop_job(
        job_id: uuid.UUID,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> StopResponse:
        new_status = await request_stop(session, job_id, user_id=user.user_id)
        if new_status is None:
            return StopResponse(ok=False)

        if new_status == JobStatus.STOP_REQUESTED:
            await append_event(
                session, job_id=job_id, kind=EventKind.STATUS, message="STOP_REQUESTED"
            )
        else:
            await append_event(
                session,
                job_id=job_id,
                kind=EventKind.STATUS,
                message="JOB_STOPPED",
                payload_json={"reason": "stop_requested_before_start"},
            )
        await session.commit()
        return StopResponse(ok=True)

    @app.get(
        "/api/jobs/{job_id}/manual-order-sizing",
        response_model=ManualLiveOrderSizingResponse,
    )
    async def manual_live_order_sizing(
        job_id: uuid.UUID,
        symbol: str = Query(..., min_length=1),
        side: Literal["LONG", "SHORT"] = Query("LONG"),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> ManualLiveOrderSizingResponse:
        job = await get_job(session, job_id, user_id=user.user_id)
        if not job:
            raise HTTPException(status_code=404, detail="Not found")
        if JobType(str(job.type)) != JobType.LIVE:
            raise HTTPException(
                status_code=422,
                detail="Manual orders are only supported for LIVE jobs.",
            )
        if JobStatus(str(job.status)) != JobStatus.RUNNING:
            raise HTTPException(status_code=409, detail="Manual orders require a RUNNING live job.")

        config = _public_job_config(job.config_json)
        env = _job_env(config)
        normalized_symbol = symbol.strip().upper()
        allowed_symbols = _job_symbols(config)
        if not normalized_symbol:
            raise HTTPException(status_code=422, detail="symbol is required")
        if allowed_symbols and normalized_symbol not in allowed_symbols:
            raise HTTPException(
                status_code=422,
                detail=f"Symbol {normalized_symbol} is not configured for this live job.",
            )

        client, should_close_client = await _resolve_manual_order_client(
            session,
            user_id=user.user_id,
            job=job,
            env=env,
        )
        try:
            sizing = await _manual_entry_sizing(
                client,
                config=config,
                symbol=normalized_symbol,
                side=side,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)[:1000]) from exc
        finally:
            if should_close_client:
                await client.aclose()

        return ManualLiveOrderSizingResponse(symbol=normalized_symbol, side=side, **sizing)

    @app.post("/api/jobs/{job_id}/manual-order", response_model=ManualLiveOrderResponse)
    async def manual_live_order(
        job_id: uuid.UUID,
        body: ManualLiveOrderRequest,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> ManualLiveOrderResponse:
        job = await get_job(session, job_id, user_id=user.user_id)
        if not job:
            raise HTTPException(status_code=404, detail="Not found")
        if JobType(str(job.type)) != JobType.LIVE:
            raise HTTPException(
                status_code=422,
                detail="Manual orders are only supported for LIVE jobs.",
            )
        if JobStatus(str(job.status)) != JobStatus.RUNNING:
            raise HTTPException(status_code=409, detail="Manual orders require a RUNNING live job.")

        config = _public_job_config(job.config_json)
        env = _job_env(config)
        symbol = body.symbol.strip().upper()
        allowed_symbols = _job_symbols(config)
        if not symbol:
            raise HTTPException(status_code=422, detail="symbol is required")
        if allowed_symbols and symbol not in allowed_symbols:
            raise HTTPException(
                status_code=422,
                detail=f"Symbol {symbol} is not configured for this live job.",
            )

        client, should_close_client = await _resolve_manual_order_client(
            session,
            user_id=user.user_id,
            job=job,
            env=env,
        )
        mark_price: float | None = None
        order_notional_usdt: float | None = None
        try:
            reduce_only = body.action == "CLOSE"
            position_before: float | None = None
            if body.action == "ENTER":
                if body.side is None:
                    raise HTTPException(status_code=422, detail="side is required for manual entry")
                sizing = await _manual_entry_sizing(
                    client,
                    config=config,
                    symbol=symbol,
                    side=body.side,
                )
                mark_price = float(sizing["mark_price"] or 0.0)
                if body.use_max:
                    quantity = float(sizing["max_quantity"] or 0.0)
                    if quantity <= 0:
                        raise HTTPException(
                            status_code=422,
                            detail="No available position size remains for this symbol.",
                        )
                elif body.notional_usdt is not None:
                    quantity = _quantity_from_notional(
                        float(body.notional_usdt),
                        mark_price,
                        {
                            "step_size": sizing.get("step_size"),
                            "max_qty": sizing.get("max_exchange_quantity"),
                        },
                    )
                elif body.quantity is not None:
                    quantity = float(body.quantity)
                else:
                    raise HTTPException(
                        status_code=422,
                        detail="notional_usdt, use_max, or quantity is required for manual entry",
                    )
                _validate_manual_entry_quantity(quantity=quantity, side=body.side, sizing=sizing)
                order_notional_usdt = quantity * mark_price
                order_side = "BUY" if body.side == "LONG" else "SELL"
                order = await client.place_order(symbol, order_side, quantity)
            else:
                raw_position = await client.fetch_position(symbol)
                position_before = _position_amt(raw_position, symbol)
                if abs(position_before) < 1e-12:
                    raise HTTPException(
                        status_code=409, detail=f"No open {symbol} position to close."
                    )
                quantity = (
                    float(body.quantity) if body.quantity is not None else abs(position_before)
                )
                if quantity - abs(position_before) > 1e-12:
                    raise HTTPException(
                        status_code=422,
                        detail="close quantity cannot exceed the current position size",
                    )
                order_side = "SELL" if position_before > 0 else "BUY"
                order = await client.place_order(
                    symbol,
                    order_side,
                    quantity,
                    reduceOnly="true",
                )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            await append_event(
                session,
                job_id=job_id,
                kind=EventKind.ORDER,
                level="ERROR",
                message="MANUAL_ORDER_FAILED",
                payload_json={
                    "action": body.action,
                    "symbol": symbol,
                    "error": str(exc)[:1000],
                    "wallet_account_id": str(job.wallet_account_id)
                    if job.wallet_account_id
                    else None,
                },
            )
            await session.commit()
            raise HTTPException(status_code=502, detail=str(exc)[:1000]) from exc
        finally:
            if should_close_client:
                await client.aclose()

        order_id = order.get("orderId")
        if order_id is not None:
            try:
                order_id_int = int(order_id)
            except (TypeError, ValueError):
                order_id_int = None
            if order_id_int is not None:
                await upsert_order(
                    session,
                    job_id=job_id,
                    symbol=symbol,
                    order_id=order_id_int,
                    side=order_side,
                    order_type=str(order.get("type") or "MARKET"),
                    status=str(order.get("status") or ""),
                    quantity=_float_or_none(order.get("origQty")) or quantity,
                    price=_float_or_none(order.get("price")),
                    executed_qty=_float_or_none(order.get("executedQty")),
                    avg_price=_float_or_none(order.get("avgPrice")),
                    raw_json={
                        **order,
                        "reason": "manual_alphaweaver",
                        "manual_action": body.action,
                    },
                )

        payload = {
            "action": body.action,
            "symbol": symbol,
            "side": order_side,
            "quantity": quantity,
            "notional_usdt": order_notional_usdt,
            "mark_price": mark_price,
            "reduce_only": reduce_only,
            "position_before": position_before,
            "wallet_account_id": str(job.wallet_account_id) if job.wallet_account_id else None,
            "order": _manual_order_payload(order),
        }
        await append_event(
            session,
            job_id=job_id,
            kind=EventKind.ORDER,
            message="MANUAL_ORDER_SUBMITTED",
            payload_json=payload,
        )
        await session.commit()
        return ManualLiveOrderResponse(
            ok=True,
            action=body.action,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            notional_usdt=order_notional_usdt,
            mark_price=mark_price,
            reduce_only=reduce_only,
            order=order,
        )

    @app.get("/api/jobs/{job_id}/events", response_model=list[JobEventResponse])
    async def events(
        job_id: uuid.UUID,
        after_event_id: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[JobEventResponse]:
        job = await get_job(session, job_id, user_id=user.user_id)
        if not job:
            raise HTTPException(status_code=404, detail="Not found")
        rows = await list_events(session, job_id=job_id, after_event_id=after_event_id, limit=limit)
        return [_event_to_response(e) for e in rows]

    @app.get("/api/jobs/{job_id}/events/stream", response_class=StreamingResponse)
    async def events_stream(
        job_id: uuid.UUID,
        after_event_id: int = Query(default=0, ge=0),
        _user: AuthenticatedUser = Depends(require_auth),
    ) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            last_id = after_event_id
            # SSE retry hint (ms)
            yield b"retry: 1000\n\n"
            try:
                while True:
                    # IMPORTANT: Do not keep a DB session open for the whole SSE connection.
                    # Each open EventSource would otherwise reserve a pooled connection indefinitely.
                    async with session_maker() as session:
                        rows = await list_events(
                            session, job_id=job_id, after_event_id=last_id, limit=200
                        )
                    if rows:
                        for ev in rows:
                            last_id = int(ev.event_id)
                            payload = _event_to_response(ev).model_dump()
                            data = json.dumps(payload, ensure_ascii=False, default=str)
                            chunk = f"id: {last_id}\ndata: {data}\n\n".encode()
                            yield chunk
                    else:
                        # keepalive
                        yield b": keepalive\n\n"
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                return

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/jobs/{job_id}/orders", response_model=list[OrderResponse])
    async def orders(
        job_id: uuid.UUID,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[OrderResponse]:
        job = await get_job(session, job_id, user_id=user.user_id)
        if not job:
            raise HTTPException(status_code=404, detail="Not found")
        rows = await list_orders(session, job_id=job_id)
        return [
            OrderResponse(
                order_id=o.order_id,
                symbol=o.symbol,
                side=o.side,
                order_type=o.order_type,
                status=o.status,
                quantity=o.quantity,
                price=o.price,
                executed_qty=o.executed_qty,
                avg_price=o.avg_price,
                ts=o.ts,
                raw=o.raw_json,
            )
            for o in rows
        ]

    @app.get("/api/jobs/{job_id}/trades", response_model=list[TradeResponse])
    async def trades(
        job_id: uuid.UUID,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[TradeResponse]:
        job = await get_job(session, job_id, user_id=user.user_id)
        if not job:
            raise HTTPException(status_code=404, detail="Not found")
        rows = await list_trades(session, job_id=job_id)
        return [
            TradeResponse(
                trade_id=t.trade_id,
                symbol=t.symbol,
                order_id=t.order_id,
                quantity=t.quantity,
                price=t.price,
                realized_pnl=t.realized_pnl,
                commission=t.commission,
                ts=t.ts,
                raw=t.raw_json,
            )
            for t in rows
        ]

    # ------------------------------------------------------------------
    # /api/me — User profile & binance keys
    # ------------------------------------------------------------------

    @app.get("/api/me")
    async def get_me(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        from control.repo import get_user_profile, list_binance_credentials

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        creds = await list_binance_credentials(session, user_id=user.user_id)
        configured_envs = [c.env for c in creds]
        return {
            "user_id": profile.user_id,
            "email": profile.email,
            "display_name": profile.display_name,
            "plan": profile.plan,
            "has_binance_keys": bool(configured_envs),
            "binance_configured_envs": configured_envs,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
        }

    def _mask_key(key: str) -> str:
        if len(key) <= 8:
            return "***"
        return key[:4] + "***" + key[-4:]

    _BINANCE_CRED_ENVS = {
        "mainnet": "https://fapi.binance.com",
        "testnet": "https://testnet.binancefuture.com",
    }

    @app.get("/api/me/binance-keys", response_model=list[BinanceCredentialStatus])
    async def get_binance_keys(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[BinanceCredentialStatus]:
        from common.crypto import get_crypto_service
        from control.repo import list_binance_credentials

        creds = await list_binance_credentials(session, user_id=user.user_id)
        cred_map = {c.env: c for c in creds}
        crypto = get_crypto_service()
        result = []
        for env in ("mainnet", "testnet"):
            cred = cred_map.get(env)
            if not cred:
                result.append(BinanceCredentialStatus(env=env, configured=False))
            else:
                try:
                    raw_key = crypto.decrypt(cred.api_key_enc)
                    masked = _mask_key(raw_key)
                except Exception:  # noqa: BLE001
                    masked = "***decryption_error***"
                ip_list = list(cred.ip_whitelist or [])
                result.append(
                    BinanceCredentialStatus(
                        env=env,
                        configured=True,
                        api_key_masked=masked,
                        ip_whitelist=ip_list,
                    )
                )
        return result

    @app.put("/api/me/binance-keys/{env}", response_model=BinanceCredentialStatus)
    async def set_binance_key(
        env: str,
        body: dict[str, Any],
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> BinanceCredentialStatus:
        if env not in _BINANCE_CRED_ENVS:
            raise HTTPException(
                status_code=422, detail=f"Invalid env. Must be one of: {list(_BINANCE_CRED_ENVS)}"
            )
        api_key = str(body.get("api_key") or "").strip()
        api_secret = str(body.get("api_secret") or "").strip()
        if not api_key or not api_secret:
            raise HTTPException(status_code=422, detail="api_key and api_secret are required")

        # Optional IP whitelist memo (operator-supplied; mainnet only).
        # Accepted shapes: list[str] or comma/whitespace-separated string.
        ip_whitelist_raw = body.get("ip_whitelist")
        ip_whitelist: list[str] | None = None
        if ip_whitelist_raw is not None:
            if isinstance(ip_whitelist_raw, str):
                parts = [p.strip() for p in ip_whitelist_raw.replace(",", " ").split()]
            elif isinstance(ip_whitelist_raw, list):
                parts = [str(p).strip() for p in ip_whitelist_raw]
            else:
                raise HTTPException(
                    status_code=422,
                    detail="ip_whitelist must be a list[str] or comma-separated string",
                )
            ip_whitelist = [p for p in parts if p]
            if env != "mainnet" and ip_whitelist:
                # Testnet keys don't have a real Binance-side IP whitelist;
                # silently drop instead of erroring so the UI can be uniform.
                ip_whitelist = []

        base_url = _BINANCE_CRED_ENVS[env]

        from binance.client import BinanceHTTPClient

        test_client = BinanceHTTPClient(
            api_key=api_key, api_secret=api_secret, base_url=base_url, timeout=10.0
        )
        try:
            account_info = await test_client.fetch_account_info()
            if not account_info:
                raise HTTPException(
                    status_code=400, detail="Binance API connection test failed: empty response"
                )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"Binance API connection test failed: {exc}"
            ) from exc
        finally:
            await test_client.aclose()

        from common.crypto import get_crypto_service

        crypto = get_crypto_service()
        api_key_enc = crypto.encrypt(api_key)
        api_secret_enc = crypto.encrypt(api_secret)

        from control.repo import upsert_binance_credential

        await upsert_binance_credential(
            session,
            user_id=user.user_id,
            env=env,
            api_key_enc=api_key_enc,
            api_secret_enc=api_secret_enc,
            ip_whitelist=ip_whitelist,
        )
        await session.commit()
        return BinanceCredentialStatus(
            env=env,
            configured=True,
            api_key_masked=_mask_key(api_key),
            ip_whitelist=ip_whitelist or [],
        )

    @app.delete("/api/me/binance-keys/{env}")
    async def delete_binance_key(
        env: str,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, bool]:
        if env not in _BINANCE_CRED_ENVS:
            raise HTTPException(
                status_code=422, detail=f"Invalid env. Must be one of: {list(_BINANCE_CRED_ENVS)}"
            )
        from control.repo import delete_binance_credential

        await delete_binance_credential(session, user_id=user.user_id, env=env)
        await session.commit()
        return {"ok": True}

    # ------------------------------------------------------------------
    # /api/me/upbit-keys — Upbit Open API credentials
    # ------------------------------------------------------------------

    @app.put("/api/me/upbit-keys")
    async def set_upbit_keys(
        body: dict[str, Any],
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        access_key = str(body.get("access_key") or "").strip()
        secret_key = str(body.get("secret_key") or "").strip()
        if not access_key or not secret_key:
            raise HTTPException(status_code=422, detail="access_key and secret_key are required")

        from upbit.client import UpbitClient, UpbitClientError

        test_client = UpbitClient(access_key=access_key, secret_key=secret_key, timeout=10.0)
        try:
            balances = await test_client.fetch_balances()
            if not isinstance(balances, list):
                raise HTTPException(
                    status_code=400, detail="Upbit API connection test failed: unexpected response"
                )
        except HTTPException:
            raise
        except UpbitClientError as exc:
            raise HTTPException(
                status_code=400, detail=f"Upbit API connection test failed: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"Upbit API connection test failed: {exc}"
            ) from exc
        finally:
            await test_client.aclose()

        from common.crypto import get_crypto_service

        crypto = get_crypto_service()
        key_enc = crypto.encrypt(access_key)
        secret_enc = crypto.encrypt(secret_key)

        from control.repo import update_user_upbit_keys

        await update_user_upbit_keys(
            session, user_id=user.user_id, api_key_enc=key_enc, api_secret_enc=secret_enc
        )
        await session.commit()
        return {"ok": True, "access_key_masked": _mask_key(access_key)}

    @app.get("/api/me/upbit-keys")
    async def get_upbit_keys(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        from control.repo import get_user_profile

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile or not profile.upbit_api_key_enc:
            return {"configured": False}

        from common.crypto import get_crypto_service

        crypto = get_crypto_service()
        try:
            raw_key = crypto.decrypt(profile.upbit_api_key_enc)
        except Exception:  # noqa: BLE001
            return {"configured": True, "access_key_masked": "***decryption_error***"}

        return {"configured": True, "access_key_masked": _mask_key(raw_key)}

    @app.delete("/api/me/upbit-keys")
    async def delete_upbit_keys(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, bool]:
        from control.repo import update_user_upbit_keys

        await update_user_upbit_keys(
            session, user_id=user.user_id, api_key_enc=None, api_secret_enc=None
        )
        await session.commit()
        return {"ok": True}

    # ------------------------------------------------------------------
    # /api/upbit/account — Upbit account info
    # ------------------------------------------------------------------

    @app.get("/api/upbit/account")
    async def get_upbit_account(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        from common.crypto import get_crypto_service
        from control.repo import get_user_profile
        from upbit.client import UpbitClient, UpbitClientError

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile or not profile.upbit_api_key_enc or not profile.upbit_api_secret_enc:
            raise HTTPException(status_code=404, detail="Upbit API keys not configured")

        crypto = get_crypto_service()
        access_key = crypto.decrypt(profile.upbit_api_key_enc)
        secret_key = crypto.decrypt(profile.upbit_api_secret_enc)

        client = UpbitClient(access_key=access_key, secret_key=secret_key)
        try:
            balances_raw = await client.fetch_balances()
            krw_usdt_price = await client.get_krw_usdt_price()
        except UpbitClientError as exc:
            raise HTTPException(status_code=502, detail=f"Upbit API error: {exc}") from exc
        finally:
            await client.aclose()

        balances = [
            {
                "currency": b.get("currency", ""),
                "balance": float(b.get("balance", 0)),
                "locked": float(b.get("locked", 0)),
            }
            for b in balances_raw
            if float(b.get("balance", 0)) + float(b.get("locked", 0)) > 0
        ]
        return {"balances": balances, "krw_usdt_price": krw_usdt_price}

    # ------------------------------------------------------------------
    # /api/bridge — Cross-exchange transfer (Upbit ↔ Binance)
    # ------------------------------------------------------------------

    def _get_upbit_client_for_user(profile: Any, crypto: Any) -> Any:
        from upbit.client import UpbitClient

        access_key = crypto.decrypt(profile.upbit_api_key_enc)
        secret_key = crypto.decrypt(profile.upbit_api_secret_enc)
        return UpbitClient(access_key=access_key, secret_key=secret_key)

    _NETWORK_BINANCE_MAP = {"TRC20": "TRX", "ERC20": "ETH", "BEP20": "BSC"}
    _NETWORK_UPBIT_MAP = {"TRC20": "TRX", "ERC20": "ETH", "BEP20": "BSC"}

    def _binance_network(code: str) -> str:
        return _NETWORK_BINANCE_MAP.get(code.upper(), code.upper())

    def _upbit_network(code: str) -> str:
        return _NETWORK_UPBIT_MAP.get(code.upper(), code.upper())

    @app.post("/api/bridge/onramp")
    async def bridge_onramp(
        body: dict[str, Any],
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        """Upbit → Binance transfer.

        Steps:
          1. (optional) Buy USDT on Upbit with KRW
          2. Get Binance deposit address
          3. Withdraw USDT from Upbit to Binance
          4. Record transfer in DB
        """

        from binance.earn_client import BinanceEarnClientError
        from common.crypto import get_crypto_service
        from control.repo import create_bridge_transfer, get_user_profile, update_bridge_transfer
        from upbit.client import UpbitClientError

        usdt_amount = float(body.get("usdt_amount") or 0)
        network = str(body.get("network") or "TRC20").upper()
        convert_from_krw: bool = bool(body.get("convert_from_krw", False))

        if usdt_amount <= 0:
            raise HTTPException(status_code=422, detail="usdt_amount must be positive")

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="User profile not found")
        if not profile.upbit_api_key_enc or not profile.upbit_api_secret_enc:
            raise HTTPException(status_code=400, detail="Upbit API keys not configured")
        from control.repo import get_binance_credential as _get_binance_cred_onramp

        binance_cred_onramp = await _get_binance_cred_onramp(
            session, user_id=user.user_id, env="mainnet"
        )
        if not binance_cred_onramp:
            raise HTTPException(status_code=400, detail="Binance mainnet API keys not configured")

        crypto = get_crypto_service()
        upbit = _get_upbit_client_for_user(profile, crypto)
        from binance.earn_client import BinanceEarnClient as _BECOnramp

        binance = _BECOnramp(
            api_key=crypto.decrypt(binance_cred_onramp.api_key_enc),
            api_secret=crypto.decrypt(binance_cred_onramp.api_secret_enc),
        )

        try:
            # Step 1: Buy USDT with KRW if requested
            if convert_from_krw:
                krw_price = await upbit.get_krw_usdt_price()
                if krw_price <= 0:
                    raise HTTPException(status_code=502, detail="Failed to get KRW-USDT price")
                krw_to_spend = round(usdt_amount * krw_price * 1.005)  # +0.5% slippage buffer
                order = await upbit.place_market_buy_krw("KRW-USDT", krw_to_spend)
                order_uuid = str(order.get("uuid", ""))
                krw_spent = float(order.get("price") or krw_to_spend)

            # Step 2: Get Binance deposit address
            deposit_info = await binance.get_deposit_address("USDT", _binance_network(network))
            deposit_address = str(deposit_info.get("address", ""))
            if not deposit_address:
                raise HTTPException(status_code=502, detail="Failed to get Binance deposit address")

            # Step 3: Withdraw from Upbit
            withdrawal = await upbit.withdraw_crypto(
                currency="USDT",
                amount=usdt_amount,
                address=deposit_address,
                net_type=_upbit_network(network),
            )
            withdrawal_uuid = str(withdrawal.get("uuid", ""))

        except HTTPException:
            raise
        except (UpbitClientError, BinanceEarnClientError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            await upbit.aclose()
            await binance.aclose()

        # Step 4: Record in DB
        transfer = await create_bridge_transfer(
            session,
            user_id=user.user_id,
            direction="UPBIT_TO_BINANCE",
            network=network,
            requested_usdt=usdt_amount,
            dst_deposit_address=deposit_address,
            krw_amount=krw_spent,
        )
        await update_bridge_transfer(
            session,
            transfer_id=transfer.id,
            status="WITHDRAWING",
            src_order_uuid=order_uuid,
            src_withdrawal_id=withdrawal_uuid,
        )
        await session.commit()

        return {
            "id": str(transfer.id),
            "status": "WITHDRAWING",
            "direction": "UPBIT_TO_BINANCE",
            "requested_usdt": usdt_amount,
            "withdrawal_uuid": withdrawal_uuid,
            "deposit_address": deposit_address,
        }

    @app.post("/api/bridge/offramp")
    async def bridge_offramp(
        body: dict[str, Any],
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        """Binance → Upbit transfer.

        Steps:
          1. (optional) Redeem from Simple Earn + transfer Futures→Spot
          2. Get Upbit deposit address
          3. Withdraw USDT from Binance to Upbit
          4. Record transfer in DB
        """
        from binance.earn_client import BinanceEarnClientError
        from common.crypto import get_crypto_service
        from control.repo import create_bridge_transfer, get_user_profile, update_bridge_transfer
        from upbit.client import UpbitClientError

        usdt_amount = float(body.get("usdt_amount") or 0)
        network = str(body.get("network") or "TRC20").upper()
        sell_to_krw: bool = bool(body.get("sell_to_krw", False))
        redeem_from_earn: bool = bool(body.get("redeem_from_earn", True))

        if usdt_amount <= 0:
            raise HTTPException(status_code=422, detail="usdt_amount must be positive")

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="User profile not found")
        if not profile.upbit_api_key_enc or not profile.upbit_api_secret_enc:
            raise HTTPException(status_code=400, detail="Upbit API keys not configured")
        from control.repo import get_binance_credential as _get_binance_cred_offramp

        binance_cred_offramp = await _get_binance_cred_offramp(
            session, user_id=user.user_id, env="mainnet"
        )
        if not binance_cred_offramp:
            raise HTTPException(status_code=400, detail="Binance mainnet API keys not configured")

        crypto = get_crypto_service()
        upbit = _get_upbit_client_for_user(profile, crypto)
        from binance.earn_client import BinanceEarnClient as _BECOfframp

        binance = _BECOfframp(
            api_key=crypto.decrypt(binance_cred_offramp.api_key_enc),
            api_secret=crypto.decrypt(binance_cred_offramp.api_secret_enc),
        )

        withdrawal_id: str | None = None
        deposit_address: str | None = None

        try:
            # Step 1: Redeem from Earn + consolidate to Spot
            if redeem_from_earn:
                earn_balance = await binance.fetch_flexible_position_usdt()
                if earn_balance > 0:
                    product_id = await binance.get_usdt_flexible_product_id()
                    if product_id:
                        redeem_amount = min(earn_balance, usdt_amount)
                        await binance.redeem(redeem_amount, product_id)

            # Step 2: Get Upbit USDT deposit address
            deposit_info = await upbit.get_deposit_address("USDT", _upbit_network(network))
            deposit_address = str(deposit_info.get("deposit_address", ""))
            if not deposit_address:
                raise HTTPException(status_code=502, detail="Failed to get Upbit deposit address")

            # Step 3: Withdraw from Binance
            result = await binance.withdraw(
                coin="USDT",
                address=deposit_address,
                amount=usdt_amount,
                network=_binance_network(network),
            )
            withdrawal_id = str(result.get("id", ""))

        except HTTPException:
            raise
        except (UpbitClientError, BinanceEarnClientError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            await upbit.aclose()
            await binance.aclose()

        # Step 4: Record in DB
        transfer = await create_bridge_transfer(
            session,
            user_id=user.user_id,
            direction="BINANCE_TO_UPBIT",
            network=network,
            requested_usdt=usdt_amount,
            dst_deposit_address=deposit_address,
        )
        await update_bridge_transfer(
            session,
            transfer_id=transfer.id,
            status="WITHDRAWING",
            src_withdrawal_id=withdrawal_id,
        )
        await session.commit()

        return {
            "id": str(transfer.id),
            "status": "WITHDRAWING",
            "direction": "BINANCE_TO_UPBIT",
            "requested_usdt": usdt_amount,
            "withdrawal_id": withdrawal_id,
            "deposit_address": deposit_address,
        }

    @app.get("/api/bridge/transfers")
    async def list_transfers(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        from control.repo import list_bridge_transfers

        transfers = await list_bridge_transfers(session, user_id=user.user_id)
        return {
            "transfers": [
                {
                    "id": str(t.id),
                    "direction": t.direction,
                    "status": t.status,
                    "network": t.network,
                    "requested_usdt": t.requested_usdt,
                    "actual_usdt": t.actual_usdt,
                    "krw_amount": t.krw_amount,
                    "fee_usdt": t.fee_usdt,
                    "src_withdrawal_id": t.src_withdrawal_id,
                    "dst_deposit_address": t.dst_deposit_address,
                    "dst_txid": t.dst_txid,
                    "error_message": t.error_message,
                    "initiated_at": t.initiated_at.isoformat(),
                    "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                    "updated_at": t.updated_at.isoformat(),
                }
                for t in transfers
            ]
        }

    @app.get("/api/bridge/transfers/{transfer_id}")
    async def get_transfer(
        transfer_id: str,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        import uuid as _uuid

        from control.repo import get_bridge_transfer

        try:
            tid = _uuid.UUID(transfer_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid transfer ID")

        transfer = await get_bridge_transfer(session, transfer_id=tid, user_id=user.user_id)
        if not transfer:
            raise HTTPException(status_code=404, detail="Transfer not found")

        return {
            "id": str(transfer.id),
            "direction": transfer.direction,
            "status": transfer.status,
            "network": transfer.network,
            "requested_usdt": transfer.requested_usdt,
            "actual_usdt": transfer.actual_usdt,
            "krw_amount": transfer.krw_amount,
            "fee_usdt": transfer.fee_usdt,
            "src_order_uuid": transfer.src_order_uuid,
            "src_withdrawal_id": transfer.src_withdrawal_id,
            "dst_deposit_address": transfer.dst_deposit_address,
            "dst_txid": transfer.dst_txid,
            "error_message": transfer.error_message,
            "initiated_at": transfer.initiated_at.isoformat(),
            "completed_at": transfer.completed_at.isoformat() if transfer.completed_at else None,
            "updated_at": transfer.updated_at.isoformat(),
        }

    @app.post("/api/bridge/transfers/{transfer_id}/sync")
    async def sync_transfer_status(
        transfer_id: str,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        """Poll withdrawal status from the source exchange and update DB."""
        import uuid as _uuid

        from common.crypto import get_crypto_service
        from control.repo import get_bridge_transfer, get_user_profile, update_bridge_transfer

        try:
            tid = _uuid.UUID(transfer_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid transfer ID")

        transfer = await get_bridge_transfer(session, transfer_id=tid, user_id=user.user_id)
        if not transfer:
            raise HTTPException(status_code=404, detail="Transfer not found")

        if transfer.status in ("COMPLETED", "FAILED"):
            return {"id": transfer_id, "status": transfer.status, "changed": False}

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="User profile not found")

        crypto = get_crypto_service()
        new_status = transfer.status
        update_kwargs: dict[str, Any] = {}

        async def _make_binance_client() -> Any:
            """Build a Binance Earn client from the user's mainnet credential."""
            from binance.earn_client import BinanceEarnClient
            from control.repo import get_binance_credential as _get_binance_cred

            cred = await _get_binance_cred(session, user_id=user.user_id, env="mainnet")
            if not cred:
                return None
            return BinanceEarnClient(
                api_key=crypto.decrypt(cred.api_key_enc),
                api_secret=crypto.decrypt(cred.api_secret_enc),
            )

        def _match_binance_deposit(deposits: Any, check_txid: str) -> dict[str, Any] | None:
            """Find a credited Binance deposit for this transfer.

            Matches by exact txid when available, otherwise falls back to
            matching the destination deposit address and requested amount so a
            credit is still detected when the source txid was never recorded.
            """
            check_txid = (check_txid or "").lower()
            dst_addr = (transfer.dst_deposit_address or "").lower()
            want_amt = float(transfer.requested_usdt or 0)
            for item in deposits if isinstance(deposits, list) else []:
                if int(item.get("status", -1)) != 1:  # 1 = Success/credited
                    continue
                item_txid = str(item.get("txId") or "")
                item_addr = str(item.get("address") or "").lower()
                item_amt = float(item.get("amount") or 0)
                match_txid = bool(check_txid) and item_txid.lower() == check_txid
                match_addr_amt = (
                    bool(dst_addr)
                    and item_addr == dst_addr
                    and want_amt > 0
                    and abs(item_amt - want_amt) <= max(1.0, want_amt * 0.02)
                )
                if match_txid or match_addr_amt:
                    return item
            return None

        try:
            if transfer.direction == "UPBIT_TO_BINANCE" and transfer.src_withdrawal_id:
                # Source side: poll Upbit withdrawal state. A failure here must
                # not block the destination-side credit check below.
                try:
                    upbit = _get_upbit_client_for_user(profile, crypto)
                    try:
                        wd = await upbit.get_withdrawal(transfer.src_withdrawal_id)
                        state = str(wd.get("state", "")).upper()
                        txid = str(wd.get("txid") or "") or transfer.dst_txid or ""
                        if state == "DONE":
                            if new_status == "WITHDRAWING":
                                new_status = "CONFIRMING"
                            if txid and not transfer.dst_txid:
                                update_kwargs["dst_txid"] = txid
                        elif state in ("REJECTED", "CANCELLED", "CANCELED", "FAILED"):
                            new_status = "FAILED"
                            update_kwargs["error_message"] = f"Upbit withdrawal {state}"
                    finally:
                        await upbit.aclose()
                except Exception as exc:  # noqa: BLE001
                    _log.warning("sync upbit withdrawal lookup failed for %s: %s", transfer_id, exc)

                # Destination side: check Binance deposit history for credit.
                if new_status in ("CONFIRMING", "WITHDRAWING"):
                    binance = await _make_binance_client()
                    if binance is not None:
                        try:
                            deposits = await binance.get_deposit_history(coin="USDT", limit=50)
                            check_txid = update_kwargs.get("dst_txid") or transfer.dst_txid or ""
                            matched = _match_binance_deposit(deposits, check_txid)
                            if matched is not None:
                                new_status = "COMPLETED"
                                update_kwargs["actual_usdt"] = float(matched.get("amount") or 0)
                                matched_txid = str(matched.get("txId") or "")
                                if matched_txid and not transfer.dst_txid:
                                    update_kwargs["dst_txid"] = matched_txid
                        finally:
                            await binance.aclose()

            elif transfer.direction == "BINANCE_TO_UPBIT" and transfer.src_withdrawal_id:
                binance = await _make_binance_client()
                if binance is None:
                    raise HTTPException(
                        status_code=400, detail="Binance mainnet API keys not configured"
                    )
                try:
                    history = await binance.get_withdrawal_history(
                        withdraw_order_id=transfer.src_withdrawal_id
                    )
                    for item in history:
                        if (
                            str(item.get("id")) == transfer.src_withdrawal_id
                            or str(item.get("withdrawOrderId")) == transfer.src_withdrawal_id
                        ):
                            status_code = int(item.get("status", -1))
                            if status_code == 6:  # Completed
                                new_status = "CONFIRMING"
                                update_kwargs["dst_txid"] = str(item.get("txId") or "")
                                update_kwargs["fee_usdt"] = float(item.get("transactionFee") or 0)
                            elif status_code in (1, 3, 5):  # Cancelled / Rejected / Failure
                                new_status = "FAILED"
                                update_kwargs["error_message"] = (
                                    f"Binance withdrawal status={status_code}"
                                )
                            break
                finally:
                    await binance.aclose()

                # Destination side: check Upbit deposit history
                if new_status in ("CONFIRMING", "WITHDRAWING") and (
                    update_kwargs.get("dst_txid") or transfer.dst_txid
                ):
                    upbit = _get_upbit_client_for_user(profile, crypto)
                    try:
                        check_txid = update_kwargs.get("dst_txid") or transfer.dst_txid
                        dep = await upbit.get_deposit(check_txid)
                        if (
                            isinstance(dep, dict)
                            and str(dep.get("state", "")).upper() == "ACCEPTED"
                        ):
                            new_status = "COMPLETED"
                            amt = dep.get("amount")
                            if amt is not None:
                                update_kwargs["actual_usdt"] = float(amt)
                    except Exception:  # noqa: BLE001
                        pass
                    finally:
                        await upbit.aclose()
        except Exception as exc:  # noqa: BLE001
            _log.warning("sync_transfer_status error for %s: %s", transfer_id, exc)

        changed = new_status != transfer.status
        if changed:
            update_kwargs["status"] = new_status
            if new_status == "COMPLETED":
                update_kwargs["completed_at"] = datetime.now(UTC)
            await update_bridge_transfer(session, transfer_id=tid, **update_kwargs)
            await session.commit()

        return {"id": transfer_id, "status": new_status, "changed": changed}

    # ------------------------------------------------------------------
    # /api/me/auto-sweep — Idle USDT → Binance Simple Earn (Flexible)
    # ------------------------------------------------------------------

    @app.get("/api/me/auto-sweep", response_model=AutoSweepStatusResponse)
    async def get_auto_sweep(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> AutoSweepStatusResponse:
        from control.repo import get_binance_credential, get_user_profile
        from live.auto_sweep_engine import get_user_status

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="User profile not found")

        mainnet_cred = await get_binance_credential(session, user_id=user.user_id, env="mainnet")

        snap = await get_user_status(session, user_id=user.user_id)
        last_run_at: datetime | None = None
        if snap and snap.get("last_run_at"):
            try:
                last_run_at = datetime.fromisoformat(str(snap["last_run_at"]))
            except Exception:  # noqa: BLE001
                last_run_at = None

        return AutoSweepStatusResponse(
            enabled=bool(profile.auto_sweep_enabled),
            futures_buffer_usdt=float(profile.auto_sweep_futures_buffer_usdt),
            sweep_threshold_usdt=float(profile.auto_sweep_sweep_threshold_usdt),
            mainnet_required=mainnet_cred is None,
            keys_configured=mainnet_cred is not None,
            futures_usdt=(snap or {}).get("futures_usdt"),
            earn_usdt=(snap or {}).get("earn_usdt"),
            last_run_at=last_run_at,
            last_action=(snap or {}).get("last_action"),
            last_error=(snap or {}).get("last_error"),
        )

    @app.put("/api/me/auto-sweep", response_model=AutoSweepStatusResponse)
    async def set_auto_sweep(
        body: AutoSweepSettingsRequest,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> AutoSweepStatusResponse:
        from control.repo import (
            get_user_profile,
            update_user_auto_sweep_settings,
        )
        from live.auto_sweep_engine import get_user_status

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="User profile not found")

        if body.enabled:
            from control.repo import get_binance_credential as _get_sweep_cred

            mainnet_cred_sweep = await _get_sweep_cred(session, user_id=user.user_id, env="mainnet")
            if not mainnet_cred_sweep:
                raise HTTPException(
                    status_code=422,
                    detail="Auto-sweep requires mainnet keys (Simple Earn is not available on testnet)",
                )

        await update_user_auto_sweep_settings(
            session,
            user_id=user.user_id,
            enabled=body.enabled,
            futures_buffer_usdt=body.futures_buffer_usdt,
            sweep_threshold_usdt=body.sweep_threshold_usdt,
        )
        await session.commit()

        if body.enabled:
            from live.auto_sweep_engine import trigger_user_sweep

            await trigger_user_sweep(session_maker, user_id=user.user_id)

        snap = await get_user_status(session, user_id=user.user_id)
        last_run_at: datetime | None = None
        if snap and snap.get("last_run_at"):
            try:
                last_run_at = datetime.fromisoformat(str(snap["last_run_at"]))
            except Exception:  # noqa: BLE001
                last_run_at = None

        return AutoSweepStatusResponse(
            enabled=body.enabled,
            futures_buffer_usdt=body.futures_buffer_usdt,
            sweep_threshold_usdt=body.sweep_threshold_usdt,
            mainnet_required=not body.enabled,
            keys_configured=body.enabled,
            futures_usdt=(snap or {}).get("futures_usdt"),
            earn_usdt=(snap or {}).get("earn_usdt"),
            last_run_at=last_run_at,
            last_action=(snap or {}).get("last_action"),
            last_error=(snap or {}).get("last_error"),
        )

    # ------------------------------------------------------------------
    # /api/binance/wallet/overview — Multi-wallet balance snapshot
    # ------------------------------------------------------------------

    @app.get("/api/binance/wallet/overview", response_model=WalletOverviewResponse)
    async def get_wallet_overview(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> WalletOverviewResponse:
        """Return Futures + Spot + Earn USDT balances in a single call."""
        import asyncio

        from binance.earn_client import BinanceEarnClient
        from common.crypto import get_crypto_service
        from control.repo import get_binance_credential

        mainnet_cred = await get_binance_credential(session, user_id=user.user_id, env="mainnet")
        if not mainnet_cred:
            return WalletOverviewResponse(
                total_usdt=0.0,
                wallets=[],
                as_of=datetime.now(UTC),
                error="Binance mainnet API keys not configured",
            )

        crypto = get_crypto_service()
        api_key = crypto.decrypt(mainnet_cred.api_key_enc)
        api_secret = crypto.decrypt(mainnet_cred.api_secret_enc)

        client = BinanceEarnClient(api_key=api_key, api_secret=api_secret)
        try:
            futures_res, spot_bal, earn_bal = await asyncio.gather(
                client.fetch_futures_wallet_balance(),
                client.fetch_spot_usdt_balance(),
                client.fetch_flexible_position_usdt(),
                return_exceptions=True,
            )
        finally:
            await client.aclose()

        def _coerce(v: object) -> float:
            if isinstance(v, BaseException):
                return 0.0
            return float(v)  # type: ignore[arg-type]

        # Futures equity = wallet balance (incl. position margin) + unrealized PnL
        if isinstance(futures_res, BaseException):
            f_wallet, f_unrealized = 0.0, 0.0
        else:
            f_wallet, f_unrealized = futures_res
        f = f_wallet + f_unrealized
        s = _coerce(spot_bal)
        e = _coerce(earn_bal)
        total = f + s + e

        def _pct(v: float) -> float:
            return round(v / total * 100, 1) if total > 0 else 0.0

        wallets = [
            WalletBalance(
                wallet="futures",
                label="USD-M Futures",
                balance_usdt=f,
                unrealized_pnl=f_unrealized,
                pct=_pct(f),
            ),
            WalletBalance(wallet="spot", label="Spot", balance_usdt=s, pct=_pct(s)),
            WalletBalance(wallet="earn", label="Simple Earn", balance_usdt=e, pct=_pct(e)),
        ]
        return WalletOverviewResponse(
            total_usdt=total,
            wallets=wallets,
            as_of=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # /api/live/positions — Multi-strategy live position board
    # ------------------------------------------------------------------

    @app.get("/api/live/positions", response_model=LivePositionsResponse)
    async def get_live_positions(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> LivePositionsResponse:
        """Group open futures positions by running LIVE strategy.

        Matches account snapshot positions to running LIVE jobs via the
        symbols configured on each job. Positions not owned by any running
        strategy are returned in ``unattributed``.
        """
        from common.crypto import get_crypto_service
        from control.repo import get_binance_credential
        from runner.account_snapshot import _fetch_snapshot

        now = datetime.now(UTC)

        cred = await get_binance_credential(session, user_id=user.user_id, env="mainnet")
        if not cred:
            return LivePositionsResponse(
                strategies=[],
                unattributed=[],
                totals=LivePositionsTotals(),
                as_of=now,
                error="Binance mainnet API keys not configured",
            )

        try:
            crypto = get_crypto_service()
            api_key = crypto.decrypt(cred.api_key_enc)
            api_secret = crypto.decrypt(cred.api_secret_enc)
        except Exception as exc:  # noqa: BLE001
            return LivePositionsResponse(
                strategies=[],
                unattributed=[],
                totals=LivePositionsTotals(),
                as_of=now,
                error=f"Failed to decrypt keys: {type(exc).__name__}",
            )

        data = await _fetch_snapshot(
            api_key=api_key,
            api_secret=api_secret,
            base_url="https://fapi.binance.com",
        )
        if not data.get("connected"):
            return LivePositionsResponse(
                strategies=[],
                unattributed=[],
                totals=LivePositionsTotals(),
                as_of=now,
                error=data.get("error") or "Failed to connect to Binance",
            )

        all_positions = [BinancePositionSummary(**p) for p in data.get("positions", [])]

        def _job_symbols(config: dict[str, Any] | None) -> list[str]:
            if not isinstance(config, dict):
                return []
            syms: list[str] = []
            streams = config.get("streams")
            if isinstance(streams, list):
                for raw in streams:
                    if isinstance(raw, dict):
                        sym = str(raw.get("symbol") or "").strip().upper()
                        if sym:
                            syms.append(sym)
            if not syms:
                sym = str(config.get("symbol") or "").strip().upper()
                if sym:
                    syms.append(sym)
            # de-dupe, preserve order
            seen: set[str] = set()
            out: list[str] = []
            for s in syms:
                if s not in seen:
                    seen.add(s)
                    out.append(s)
            return out

        active_jobs = [
            j
            for j in await list_jobs(
                session, user_id=user.user_id, job_type=JobType.LIVE, limit=128
            )
            if j.status in (JobStatus.RUNNING, JobStatus.STOP_REQUESTED)
        ]

        claimed: set[str] = set()  # "SYMBOL-SIDE" already attributed
        strategies: list[LiveStrategyPositions] = []

        for job in active_jobs:
            config = job.config_json if isinstance(job.config_json, dict) else {}
            symbols = _job_symbols(config)
            matched: list[BinancePositionSummary] = []
            for pos in all_positions:
                key = f"{pos.symbol}-{pos.side}"
                if pos.symbol.upper() in symbols and key not in claimed:
                    matched.append(pos)
                    claimed.add(key)
            strategy_name = Path(job.strategy_path).stem if job.strategy_path else job.job_id
            allocated = float(config.get("initial_balance") or 0.0)
            strategies.append(
                LiveStrategyPositions(
                    job_id=str(job.job_id),
                    strategy_path=job.strategy_path or "",
                    strategy_name=strategy_name,
                    status=str(job.status),
                    symbols=symbols,
                    allocated_usdt=allocated,
                    positions=matched,
                    position_count=len(matched),
                    total_notional=sum(abs(p.notional) for p in matched),
                    total_unrealized_pnl=sum(p.unrealized_pnl for p in matched),
                )
            )

        unattributed = [p for p in all_positions if f"{p.symbol}-{p.side}" not in claimed]

        totals = LivePositionsTotals(
            strategy_count=len(strategies),
            open_position_count=len(all_positions),
            total_notional=sum(abs(p.notional) for p in all_positions),
            total_unrealized_pnl=sum(p.unrealized_pnl for p in all_positions),
        )

        return LivePositionsResponse(
            strategies=strategies,
            unattributed=unattributed,
            totals=totals,
            as_of=now,
        )

    # ------------------------------------------------------------------
    # /api/billing — Stripe 결제
    # ------------------------------------------------------------------

    @app.post("/api/billing/checkout")
    async def billing_checkout(
        body: dict[str, Any],
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        import stripe as stripe_lib

        stripe_settings = settings.stripe
        if not stripe_settings.secret_key:
            raise HTTPException(status_code=500, detail="Stripe is not configured")

        stripe_lib.api_key = stripe_settings.secret_key
        plan = str(body.get("plan") or "pro").strip().lower()
        price_map = {
            "pro": stripe_settings.price_id_pro,
            "enterprise": stripe_settings.price_id_enterprise,
        }
        price_id = price_map.get(plan)
        if not price_id:
            raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

        from control.repo import get_user_profile

        profile = await get_user_profile(session, user_id=user.user_id)
        customer_id = profile.stripe_customer_id if profile else None
        checkout_params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": f"{settings.frontend_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{settings.frontend_url}/billing/cancel",
            "allow_promotion_codes": True,
            "metadata": {"user_id": user.user_id},
        }
        if customer_id:
            checkout_params["customer"] = customer_id
        else:
            checkout_params["customer_email"] = user.email

        cs = stripe_lib.checkout.Session.create(**checkout_params)
        return {"checkout_url": cs.url, "session_id": cs.id}

    @app.post("/api/billing/portal")
    async def billing_portal(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, str]:
        import stripe as stripe_lib

        stripe_settings = settings.stripe
        if not stripe_settings.secret_key:
            raise HTTPException(status_code=500, detail="Stripe is not configured")
        stripe_lib.api_key = stripe_settings.secret_key

        from control.repo import get_user_profile

        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile or not profile.stripe_customer_id:
            raise HTTPException(
                status_code=400, detail="No billing account found. Subscribe first."
            )

        portal = stripe_lib.billing_portal.Session.create(
            customer=profile.stripe_customer_id,
            return_url=f"{settings.frontend_url}/billing",
        )
        return {"portal_url": portal.url}

    @app.get("/api/billing/status")
    async def billing_status(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        from api.plans import get_plan_limits
        from control.repo import get_usage_count, get_user_profile

        profile = await get_user_profile(session, user_id=user.user_id)
        plan = profile.plan if profile else "free"
        limits = get_plan_limits(plan)

        from datetime import datetime as dt

        period = dt.now(UTC).strftime("%Y-%m")
        bt_used = await get_usage_count(
            session, user_id=user.user_id, action="backtest", period_key=period
        )
        llm_used = await get_usage_count(
            session, user_id=user.user_id, action="llm_generate", period_key=period
        )

        return {
            "plan": plan,
            "limits": {
                "max_live_jobs": limits.max_live_jobs,
                "max_backtest_per_month": limits.max_backtest_per_month,
                "max_llm_generate_per_month": limits.max_llm_generate_per_month,
                "portfolio_mode": limits.portfolio_mode,
            },
            "usage": {
                "backtest_this_month": bt_used,
                "llm_generate_this_month": llm_used,
            },
            "plan_expires_at": profile.plan_expires_at.isoformat()
            if profile and profile.plan_expires_at
            else None,
        }

    @app.post("/api/billing/webhook")
    async def billing_webhook(request: Any) -> dict[str, str]:
        import stripe as stripe_lib

        stripe_settings = settings.stripe
        if not stripe_settings.secret_key or not stripe_settings.webhook_secret:
            raise HTTPException(status_code=500, detail="Stripe webhook is not configured")

        stripe_lib.api_key = stripe_settings.secret_key
        payload = await request.body()
        sig = request.headers.get("stripe-signature", "")

        try:
            event = stripe_lib.Webhook.construct_event(payload, sig, stripe_settings.webhook_secret)
        except (ValueError, stripe_lib.error.SignatureVerificationError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid webhook: {exc}") from exc

        event_type = event["type"]
        data_obj = event["data"]["object"]

        async with session_maker() as session:
            if event_type == "checkout.session.completed":
                user_id = (data_obj.get("metadata") or {}).get("user_id")
                customer_id = data_obj.get("customer")
                subscription_id = data_obj.get("subscription")
                if user_id and customer_id:
                    from control.repo import update_user_plan

                    plan = "pro"
                    if data_obj.get("metadata", {}).get("plan"):
                        plan = data_obj["metadata"]["plan"]
                    await update_user_plan(
                        session,
                        user_id=user_id,
                        plan=plan,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=subscription_id,
                    )
                    await session.commit()

            elif event_type == "customer.subscription.updated":
                customer_id = data_obj.get("customer")
                if customer_id:
                    from control.repo import get_user_by_stripe_customer_id, update_user_plan

                    profile = await get_user_by_stripe_customer_id(
                        session, stripe_customer_id=customer_id
                    )
                    if profile:
                        status = data_obj.get("status")
                        if status in ("active", "trialing"):
                            items = data_obj.get("items", {}).get("data", [])
                            price_id = items[0]["price"]["id"] if items else ""
                            plan = "pro"
                            if price_id == stripe_settings.price_id_enterprise:
                                plan = "enterprise"
                            await update_user_plan(session, user_id=profile.user_id, plan=plan)
                        elif status in ("past_due", "unpaid"):
                            pass
                        await session.commit()

            elif event_type == "customer.subscription.deleted":
                customer_id = data_obj.get("customer")
                if customer_id:
                    from datetime import datetime as dt
                    from datetime import timedelta

                    from control.repo import get_user_by_stripe_customer_id, update_user_plan

                    profile = await get_user_by_stripe_customer_id(
                        session, stripe_customer_id=customer_id
                    )
                    if profile:
                        grace = dt.now(UTC) + timedelta(days=3)
                        await update_user_plan(
                            session,
                            user_id=profile.user_id,
                            plan="free",
                            plan_expires_at=grace,
                        )
                        await session.commit()

            elif event_type == "invoice.payment_failed":
                pass

        return {"status": "ok"}

    # ------------------------------------------------------------------
    # /api/auth — Registration & credential verification (no auth required)
    # ------------------------------------------------------------------

    @app.post("/api/auth/register")
    async def register(
        request: Request,
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        import secrets

        import bcrypt

        from control.models import UserProfile
        from notifications.email import send_verification_email

        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        display_name = (body.get("displayName") or email.split("@")[0])[:100]

        if not email or not password:
            raise HTTPException(status_code=400, detail="Email and password are required")
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

        # Check if email already exists
        existing = await session.execute(
            select(UserProfile).where(UserProfile.email == email).limit(1)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

        user_id = f"cred-{email}"
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        verification_token = secrets.token_urlsafe(48)

        profile = UserProfile(
            user_id=user_id,
            email=email,
            display_name=display_name,
            password_hash=hashed,
            email_verified=False,
            email_verification_token=verification_token,
        )
        session.add(profile)
        await session.commit()

        # Send verification email
        frontend_url = settings.frontend_url.rstrip("/")
        verify_url = f"{frontend_url}/auth/verify-email?token={verification_token}&email={email}"
        await send_verification_email(to_email=email, user_name=display_name, verify_url=verify_url)

        return {"ok": True, "user_id": user_id, "email": email}

    @app.get("/api/auth/verify-email")
    async def verify_email(
        token: str = Query(...),
        email: str = Query(...),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        from control.models import UserProfile

        normalized_email = email.strip().lower()
        result = await session.execute(
            select(UserProfile)
            .where(
                UserProfile.email == normalized_email,
                UserProfile.email_verification_token == token,
            )
            .limit(1)
        )
        profile = result.scalar_one_or_none()

        if not profile:
            raise HTTPException(status_code=400, detail="Invalid or expired verification link")

        if profile.email_verified:
            return {"ok": True, "already_verified": True}

        profile.email_verified = True
        profile.email_verification_token = None
        await session.commit()

        return {"ok": True, "already_verified": False}

    @app.post("/api/auth/resend-verification")
    async def resend_verification(
        request: Request,
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        import secrets

        from control.models import UserProfile
        from notifications.email import send_verification_email

        body = await request.json()
        email = (body.get("email") or "").strip().lower()

        if not email:
            raise HTTPException(status_code=400, detail="Email is required")

        result = await session.execute(
            select(UserProfile).where(UserProfile.email == email).limit(1)
        )
        profile = result.scalar_one_or_none()

        if not profile or profile.email_verified:
            # Don't reveal whether account exists
            return {"ok": True}

        verification_token = secrets.token_urlsafe(48)
        profile.email_verification_token = verification_token
        await session.commit()

        frontend_url = settings.frontend_url.rstrip("/")
        verify_url = f"{frontend_url}/auth/verify-email?token={verification_token}&email={email}"
        await send_verification_email(
            to_email=email,
            user_name=profile.display_name or email.split("@")[0],
            verify_url=verify_url,
        )

        return {"ok": True}

    @app.post("/api/auth/verify-credentials")
    async def verify_credentials(
        request: Request,
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        import bcrypt

        from control.models import UserProfile

        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""

        if not email or not password:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        result = await session.execute(
            select(UserProfile).where(UserProfile.email == email).limit(1)
        )
        profile = result.scalar_one_or_none()

        if not profile or not profile.password_hash:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not bcrypt.checkpw(password.encode("utf-8"), profile.password_hash.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not profile.email_verified:
            raise HTTPException(status_code=403, detail="Email not verified")

        return {
            "ok": True,
            "user_id": profile.user_id,
            "email": profile.email,
            "display_name": profile.display_name,
        }

    from api.wallets import register_wallet_routes

    register_wallet_routes(
        app,
        require_auth_dep=require_auth,
        db_session_dep=_db_session,
    )

    from api.transfers import register_transfer_routes

    register_transfer_routes(
        app,
        require_auth_dep=require_auth,
        db_session_dep=_db_session,
    )

    return app


app = create_app()
