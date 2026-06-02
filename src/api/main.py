from __future__ import annotations

import asyncio
import ast
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from control.alembic_upgrade import run_alembic_upgrade_head
from control.db import create_async_engine, create_session_maker, init_db
from control.enums import EventKind, JobStatus, JobType
from control.models import Job
from control.repo import (
    append_event,
    create_job,
    create_strategy_quality_log,
    delete_strategy_meta_by_name,
    delete_strategy_chat_session as repo_delete_strategy_chat_session,
    delete_job,
    delete_jobs,
    get_account_snapshot,
    get_job,
    get_strategy_meta_by_name,
    list_events,
    count_jobs,
    list_jobs,
    list_job_summaries,
    list_orders,
    list_strategy_meta,
    list_strategy_chat_sessions as repo_list_strategy_chat_sessions,
    list_strategy_chat_session_summaries as repo_list_strategy_chat_session_summaries,
    get_strategy_chat_session as repo_get_strategy_chat_session,
    list_strategy_quality_logs,
    list_trades,
    list_trades_batch,
    request_stop,
    stop_all_jobs,
    upsert_strategy_meta,
    upsert_strategy_chat_session as repo_upsert_strategy_chat_session,
)
from common.strategy_storage import get_strategy_storage
from settings import get_settings
from llm.client import LLMClient
try:
    from llm.capability_registry import (
        SUPPORTED_DATA_SOURCES as LOCAL_SUPPORTED_DATA_SOURCES,
        SUPPORTED_INDICATOR_SCOPES as LOCAL_SUPPORTED_INDICATOR_SCOPES,
        SUPPORTED_CONTEXT_METHODS as LOCAL_SUPPORTED_CONTEXT_METHODS,
        UNSUPPORTED_CAPABILITY_RULES as LOCAL_UNSUPPORTED_CAPABILITY_RULES,
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
    HealthResponse,
    CountItem,
    DeleteAllResponse,
    DeleteResponse,
    JobPolicyCheckRequest,
    JobPolicyCheckResponse,
    JobCreateRequest,
    JobCountsResponse,
    JobEventResponse,
    JobResponse,
    JobSummary,
    OrderResponse,
    StopResponse,
    StopAllResponse,
    StrategyInfo,
    StrategyContentResponse,
    StrategyIntakeRequest,
    StrategyIntakeResponse,
    StrategyCapabilityResponse,
    StrategyQualitySummaryResponse,
    StrategyGenerateRequest,
    StrategyGenerateResponse,
    StrategyChatSessionResponse,
    StrategyChatSessionSummary,
    StrategyChatSessionUpsertRequest,
    StrategyChatRequest,
    StrategyChatResponse,
    StrategySyntaxCheckRequest,
    StrategySyntaxCheckResponse,
    StrategySyntaxError,
    StrategySaveRequest,
    StrategySaveResponse,
    StrategyParamsApplyRequest,
    StrategyParamsApplyResponse,
    StrategyParamsExtractRequest,
    StrategyParamsExtractResponse,
    LlmTestRequest,
    LlmTestResponse,
    AdminUserItem,
    AdminUsersResponse,
    TradeResponse,
    BinanceAssetBalance,
    BinanceCredentialStatus,
    BinancePositionSummary,
    BinanceAccountSummaryResponse,
    QuickBacktestRequest,
    QuickBacktestResponse,
    PortfolioSummaryResponse,
    WalletSnapshot,
    AllocationSlice,
    StrategyModuleCatalogResponse,
    StrategyModuleStatus,
    FundingArbitrageParams,
    FundingArbitrageStatusResponse,
    FundingScreenerItem,
    FundingScreenerResponse,
    AutoSweepSettingsRequest,
    AutoSweepStatusResponse,
    WalletBalance,
    WalletOverviewResponse,
    LiveStrategyPositions,
    LivePositionsTotals,
    LivePositionsResponse,
)
from api.strategy_catalog import list_strategy_files, validate_strategy_path
from api.strategy_params import (
    StrategyParamsError,
    apply_strategy_params,
    extract_strategy_params,
)

INTERNAL_JOB_CONFIG_KEYS = {"_strategy_code"}

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
        _log.warning("Failed to resolve deadband for %s hold_days=%s", symbol, hold_days, exc_info=True)
        return None


# 펀딩 차익거래는 현물 롱 + 선물 숏 구조이므로 후보 심볼은 반드시 현물 시장에도 상장돼야 한다.
# Binance 데모(testnet) 현물은 mainnet 현물 상장 목록을 그대로 미러링하므로, mainnet 현물
# universe를 두 환경 공통 필터로 사용한다. exchangeInfo 페이로드가 크므로 1시간 캐시한다.
_SPOT_SYMBOLS_CACHE: dict[str, Any] = {"symbols": set(), "ts": 0.0}
_SPOT_SYMBOLS_TTL = 3600.0


async def _fetch_tradable_spot_symbols() -> set[str]:
    """현재 거래(TRADING) 가능한 현물 심볼 집합을 반환(1시간 캐시).

    조회 실패 시 마지막으로 캐시된 집합(없으면 빈 집합)을 반환하여, 스크리너가
    필터 때문에 전부 비는 일이 없도록 한다(빈 집합이면 호출 측에서 필터를 건너뜀).
    """
    import time as _time

    now = _time.time()
    cached = _SPOT_SYMBOLS_CACHE
    if cached["symbols"] and (now - cached["ts"]) < _SPOT_SYMBOLS_TTL:
        return cached["symbols"]
    try:
        async with httpx.AsyncClient(base_url="https://api.binance.com", timeout=10.0) as client:
            resp = await client.get("/api/v3/exchangeInfo")
            resp.raise_for_status()
            syms = {
                s["symbol"]
                for s in resp.json().get("symbols", [])
                if isinstance(s, dict) and s.get("status") == "TRADING"
            }
        if syms:
            _SPOT_SYMBOLS_CACHE["symbols"] = syms
            _SPOT_SYMBOLS_CACHE["ts"] = now
        return syms
    except Exception:
        _log.warning("Failed to fetch spot symbols for screener filter", exc_info=True)
        return cached["symbols"]


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
    unsupported = [str(getattr(rule, "name", "")).strip() for rule in LOCAL_UNSUPPORTED_CAPABILITY_RULES]
    return {
        "supported_data_sources": [str(v).strip() for v in LOCAL_SUPPORTED_DATA_SOURCES if str(v).strip()],
        "supported_indicator_scopes": [
            str(v).strip() for v in LOCAL_SUPPORTED_INDICATOR_SCOPES if str(v).strip()
        ],
        "supported_context_methods": [str(v).strip() for v in LOCAL_SUPPORTED_CONTEXT_METHODS if str(v).strip()],
        "unsupported_categories": [v for v in unsupported if v],
        "summary_lines": [str(v).strip() for v in local_capability_summary_lines() if str(v).strip()],
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
        p.name: StrategyInfo(name=p.name, path=str(p.relative_to(root)))
        for p in files
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
        meta = await get_strategy_meta_by_name(session, user_id=user.user_id, strategy_name=strategy_name)
        if meta is not None:
            try:
                return strategy_name, storage.download_by_path(meta.blob_path)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"Failed to read strategy object: {exc}") from exc

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
        meta = await get_strategy_meta_by_name(session, user_id=user.user_id, strategy_name=strategy_name)
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



_INTAKE_ALLOWED_STATUSES = {"READY", "NEEDS_CLARIFICATION", "UNSUPPORTED_CAPABILITY", "OUT_OF_SCOPE"}
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


async def _run_intake(client: LLMClient, prompt: str, messages: list[dict[str, str]] | None) -> StrategyIntakeResponse:
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
        if (
            isinstance(obj, type)
            and name.endswith("Strategy")
            and name != "Strategy"
        ):
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
        from sqlalchemy.exc import OperationalError, InterfaceError

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

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        nonlocal _keepalive_task, _runner_task, _runner_worker, _db_init_task
        try:
            from live.auto_sweep_engine import stop_engine as _stop_auto_sweep

            await _stop_auto_sweep()
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
        from sqlalchemy.exc import OperationalError, InterfaceError

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with session_maker() as session:
                    yield session
                    return
            except (OperationalError, InterfaceError, OSError) as exc:
                if attempt < max_retries:
                    _logger.warning("DB session error (attempt %d/%d): %s", attempt + 1, max_retries + 1, exc)
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
                    unsupported_requirements=list(intake.unsupported_requirements) if intake else [],
                    development_requirements=list(intake.development_requirements) if intake else [],
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
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> BinanceAccountSummaryResponse:
        from control.repo import get_user_profile

        profile = await get_user_profile(session, user_id=user.user_id)
        from control.repo import get_binance_credential

        cred = await get_binance_credential(session, user_id=user.user_id, env="mainnet")
        if not cred:
            return BinanceAccountSummaryResponse(
                configured=False,
                connected=False,
                mode="mainnet",
                base_url="",
                error="Binance mainnet API keys are not configured. Go to Settings to set up your keys.",
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
                mode="mainnet",
                base_url="https://fapi.binance.com",
                error=f"Failed to decrypt keys: {type(exc).__name__}",
            )

        base_url = "https://fapi.binance.com"

        from runner.account_snapshot import _fetch_snapshot
        data = await _fetch_snapshot(api_key=api_key, api_secret=api_secret, base_url=base_url)

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

    @app.get("/api/portfolio/summary", response_model=PortfolioSummaryResponse)
    async def portfolio_summary(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> PortfolioSummaryResponse:
        """전체 AUM + 전략 카테고리별 자산 배분 요약."""
        from control.repo import get_user_profile
        from datetime import date

        now = datetime.now(timezone.utc)
        futures_balance = 0.0
        futures_unrealized = 0.0

        profile = await get_user_profile(session, user_id=user.user_id)
        from control.repo import get_binance_credential
        mainnet_cred = await get_binance_credential(session, user_id=user.user_id, env="mainnet") if profile else None
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
                slices.append(AllocationSlice(
                    category="Directional_Alpha",
                    allocated_usdt=directional_alloc,
                    pct=round(directional_alloc / total_aum * 100, 1),
                ))
            if cash > 0:
                slices.append(AllocationSlice(
                    category="Cash",
                    allocated_usdt=cash,
                    pct=round(cash / total_aum * 100, 1),
                ))
        else:
            slices.append(AllocationSlice(category="Cash", allocated_usdt=0.0, pct=100.0))

        return PortfolioSummaryResponse(
            total_aum_usdt=total_aum,
            total_unrealized_pnl=futures_unrealized,
            total_realized_pnl_today=realized_today,
            wallets=[WalletSnapshot(wallet="futures", balance_usdt=futures_balance, unrealized_pnl=futures_unrealized)],
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
        top_n: int = 5,
        user: AuthenticatedUser = Depends(require_auth),  # noqa: ARG001
    ) -> FundingScreenerResponse:
        """현재 펀딩비가 높고 통계적으로 유리한 종목 Top-N 스크리너.

        Redis에 캐시된 AR(1)/OU half-life 통계를 읽고, Binance /fapi/v1/premiumIndex로
        실시간 펀딩비를 조회하여 score = current_rate / entry_threshold 기준으로 정렬.
        """
        import json as _json
        from datetime import timezone

        ROUNDTRIP = _FUNDING_ROUNDTRIP_COST
        DEFAULT_INTERVAL_H = 8.0
        PPY = (365 * 24) / DEFAULT_INTERVAL_H  # 1095 (정산 횟수/년)

        rd = _make_funding_redis()
        if rd is None:
            return FundingScreenerResponse(
                items=[], roundtrip_cost_pct=ROUNDTRIP * 100,
                error="Redis가 구성되지 않았습니다.",
                as_of=datetime.now(timezone.utc),
            )

        try:
            univ_raw = rd.get("funding:stats:_universe")
            if not univ_raw:
                return FundingScreenerResponse(
                    items=[], roundtrip_cost_pct=ROUNDTRIP * 100,
                    error="펀딩 통계 데이터가 아직 없습니다. oi-ingestor가 첫 수집을 완료할 때까지 기다려 주세요.",
                    as_of=datetime.now(timezone.utc),
                )
            parsed = _json.loads(univ_raw)
            if isinstance(parsed, dict):
                universe: list[str] = list(parsed.get("symbols", []))
            else:
                universe = list(parsed)
        except Exception as exc:
            return FundingScreenerResponse(
                items=[], roundtrip_cost_pct=ROUNDTRIP * 100,
                error=str(exc), as_of=datetime.now(timezone.utc),
            )

        # Redis MGET로 모든 종목 통계를 한 번에 읽기
        keys = [f"funding:stats:{sym}" for sym in universe]
        try:
            raw_vals = rd.mget(keys)
        except Exception as exc:
            return FundingScreenerResponse(
                items=[], roundtrip_cost_pct=ROUNDTRIP * 100,
                error=str(exc), as_of=datetime.now(timezone.utc),
            )

        stats_map: dict[str, dict] = {}
        for sym, raw in zip(universe, raw_vals):
            if raw:
                try:
                    stats_map[sym] = _json.loads(raw)
                except Exception:
                    pass

        # Binance /fapi/v1/premiumIndex (인수 없이 호출 시 전체 종목 반환)
        try:
            async with httpx.AsyncClient(
                base_url="https://fapi.binance.com", timeout=10.0
            ) as client:
                resp = await client.get("/fapi/v1/premiumIndex")
                resp.raise_for_status()
                rates: dict[str, float] = {
                    row["symbol"]: float(row.get("lastFundingRate", 0))
                    for row in resp.json()
                    if isinstance(row, dict)
                }
        except Exception as exc:
            return FundingScreenerResponse(
                items=[], roundtrip_cost_pct=ROUNDTRIP * 100,
                error=f"Binance API 오류: {exc}", as_of=datetime.now(timezone.utc),
            )

        # 현물 시장에도 상장된 심볼만 후보로 사용 (현물 롱 + 선물 숏 모두 체결 가능해야 함).
        spot_symbols = await _fetch_tradable_spot_symbols()

        items: list[FundingScreenerItem] = []
        for sym, stat in stats_map.items():
            hl = float(stat.get("half_life_settlements") or 0)
            if hl <= 0:
                continue
            current_rate = rates.get(sym, 0.0)
            if current_rate <= 0:
                continue
            # 현물 미상장(선물 전용) 심볼은 차익거래 불가 → 제외. (필터 조회 실패 시 건너뜀)
            if spot_symbols and sym not in spot_symbols:
                continue
            entry_threshold_pct = (ROUNDTRIP / hl) * 100
            score = (current_rate * 100) / entry_threshold_pct
            items.append(FundingScreenerItem(
                symbol=sym,
                current_rate_pct=round(current_rate * 100, 5),
                annualized_pct=round(current_rate * PPY * 100, 2),
                half_life_settlements=round(hl, 2),
                entry_threshold_pct=round(entry_threshold_pct, 5),
                score=round(score, 2),
                avg_rate_pct=round(float(stat.get("avg_rate", 0.0)), 5),
                n_samples=int(stat.get("n_samples", 0)),
            ))

        items.sort(key=lambda x: x.score, reverse=True)
        return FundingScreenerResponse(
            items=items[:top_n],
            roundtrip_cost_pct=round(ROUNDTRIP * 100, 2),
            as_of=datetime.now(timezone.utc),
        )

    @app.post("/api/funding-arb/start", response_model=FundingArbitrageStatusResponse)
    async def funding_arb_start(
        params: FundingArbitrageParams,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> FundingArbitrageStatusResponse:
        """펀딩비 차익거래 봇 시작."""
        from control.repo import get_binance_credential
        from live.funding_arbitrage_engine import (
            start_engine,
            get_engine_status_persisted,
        )
        from common.crypto import get_crypto_service

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
                raise HTTPException(status_code=400, detail="Testnet(Demo) API 키가 설정되지 않았습니다.")
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
                params = params.model_copy(update={
                    "entry_deadband_pct": entry_pct,
                    "exit_deadband_pct": exit_pct,
                })
                logging.getLogger("api").info(
                    "Dynamic deadband resolved for %s hold_days=%d: entry=%.5f%% exit=%.5f%%",
                    params.symbol, params.hold_days, entry_pct, exit_pct,
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
        from live.funding_arbitrage_engine import stop_engine, get_engine_status_persisted
        await stop_engine(user.user_id, session_maker=session_maker)
        return await get_engine_status_persisted(session, user.user_id)

    @app.get(
        "/api/binance/futures/symbols",
        response_model=list[str],
        dependencies=[Depends(require_auth)],
    )
    async def list_binance_futures_symbols() -> list[str]:
        now = time.monotonic()
        cached_symbols = futures_symbols_cache.get("symbols", [])
        if now < float(futures_symbols_cache.get("expires_at", 0.0)) and isinstance(cached_symbols, list):
            return [str(item) for item in cached_symbols if isinstance(item, str)]

        from binance.client import normalize_binance_base_url

        base_url = normalize_binance_base_url(
            settings.binance.base_url_backtest or settings.binance.base_url or "https://fapi.binance.com"
        )

        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                response = await client.get("/fapi/v1/exchangeInfo")
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            if isinstance(cached_symbols, list) and cached_symbols:
                return [str(item) for item in cached_symbols if isinstance(item, str)]
            raise HTTPException(status_code=502, detail=f"Failed to fetch Binance futures symbols: {exc}") from exc

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

    @app.get("/api/strategies", response_model=list[StrategyInfo], dependencies=[Depends(require_auth)])
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

        openai_messages = [{"role": m.role, "content": m.content} for m in messages] if messages else None
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
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await list_strategy_quality_logs(session, since=since, limit=limit)

        total_requests = len(rows)
        intake_only_requests = sum(1 for r in rows if r.endpoint == "intake")
        generate_rows = [r for r in rows if r.endpoint in {"generate", "generate_stream"}]
        generate_requests = len(generate_rows)
        generation_success_count = sum(1 for r in generate_rows if r.generation_success is True)
        generation_failure_count = sum(
            1 for r in generate_rows if r.generation_attempted is True and r.generation_success is False
        )
        repaired_count = sum(1 for r in generate_rows if r.repaired is True)
        total_repair_attempts = sum(int(r.repair_attempts or 0) for r in generate_rows)

        ready_count = sum(1 for r in rows if str(r.status or "").upper() == "READY")
        clarification_count = sum(1 for r in rows if str(r.status or "").upper() == "NEEDS_CLARIFICATION")
        unsupported_count = sum(1 for r in rows if str(r.status or "").upper() == "UNSUPPORTED_CAPABILITY")
        out_of_scope_count = sum(1 for r in rows if str(r.status or "").upper() == "OUT_OF_SCOPE")

        missing_counter: Counter[str] = Counter()
        unsupported_req_counter: Counter[str] = Counter()
        error_stage_counter: Counter[str] = Counter()

        for row in rows:
            for item in (row.missing_fields or []):
                key = str(item).strip()
                if key:
                    missing_counter[key] += 1
            for item in (row.unsupported_requirements or []):
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

        result = await session.execute(
            select(UserProfile).order_by(UserProfile.created_at.desc())
        )
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

            openai_messages = [{"role": m.role, "content": m.content} for m in messages] if messages else None
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

        openai_messages = [{"role": m.role, "content": m.content} for m in messages] if messages else None

        code_acc: list[str] = []
        stream_repaired = False
        stream_repair_attempts = 0
        try:
            if messages:
                stream = client.generate_strategy_stream("", messages=openai_messages, confirmed_plan=body.confirmed_plan)
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
        rows = await repo_list_strategy_chat_session_summaries(session, user_id=user_id, limit=limit)
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
            raise HTTPException(status_code=502, detail=f"Strategy verification failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            _cleanup_verify_temp(temp_path)
            raise HTTPException(status_code=502, detail=f"Strategy verification failed: {exc}") from exc

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
                raise HTTPException(status_code=500, detail=f"Failed to upload strategy object: {exc}") from exc
            return StrategySaveResponse(path=_logical_strategy_path(filename))

        dirs = _strategy_dirs()
        if not dirs:
            raise HTTPException(status_code=500, detail="STRATEGY_DIRS is not configured")

        base_dir = dirs[0]
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Failed to prepare strategy dir: {exc}") from exc

        final_target = _unique_strategy_path(base_dir, filename)
        try:
            final_target.write_text(code, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Failed to write strategy file: {exc}") from exc
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
                    column=(exc.offset - 1) if isinstance(exc.offset, int) and exc.offset > 0 else exc.offset,
                    end_line=getattr(exc, "end_lineno", None),
                    end_column=(
                        (exc.end_offset - 1)
                        if isinstance(getattr(exc, "end_offset", None), int) and getattr(exc, "end_offset", None) > 0
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
        return JobPolicyCheckResponse(ok=result.ok, blockers=result.blockers, warnings=result.warnings)

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
        )
        await append_event(
            session,
            job_id=job.job_id,
            kind=EventKind.STATUS,
            message="JOB_CREATED",
            payload_json={"type": str(body.type), "strategy_path": body.strategy_path},
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
                    trade_id=t.trade_id, symbol=t.symbol, order_id=t.order_id,
                    quantity=t.quantity, price=t.price, realized_pnl=t.realized_pnl,
                    commission=t.commission, ts=t.ts, raw=t.raw_json,
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
                                    "status": j.status.value if hasattr(j.status, "value") else str(j.status),
                                    "strategy_path": j.strategy_path,
                                    "config": _public_job_config(j.config),
                                    "started_at": j.started_at.isoformat() if j.started_at else None,
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
                    yield f"data: {data}\n\n".encode("utf-8")
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
            await append_event(session, job_id=job_id, kind=EventKind.STATUS, message="STOP_REQUESTED")
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
                        rows = await list_events(session, job_id=job_id, after_event_id=last_id, limit=200)
                    if rows:
                        for ev in rows:
                            last_id = int(ev.event_id)
                            payload = _event_to_response(ev).model_dump()
                            data = json.dumps(payload, ensure_ascii=False, default=str)
                            chunk = f"id: {last_id}\ndata: {data}\n\n".encode("utf-8")
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
                order_id=o.order_id, symbol=o.symbol, side=o.side,
                order_type=o.order_type, status=o.status, quantity=o.quantity,
                price=o.price, executed_qty=o.executed_qty, avg_price=o.avg_price,
                ts=o.ts, raw=o.raw_json,
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
                trade_id=t.trade_id, symbol=t.symbol, order_id=t.order_id,
                quantity=t.quantity, price=t.price, realized_pnl=t.realized_pnl,
                commission=t.commission, ts=t.ts, raw=t.raw_json,
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
        from control.repo import list_binance_credentials
        from common.crypto import get_crypto_service
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
                result.append(BinanceCredentialStatus(env=env, configured=True, api_key_masked=masked))
        return result

    @app.put("/api/me/binance-keys/{env}", response_model=BinanceCredentialStatus)
    async def set_binance_key(
        env: str,
        body: dict[str, Any],
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> BinanceCredentialStatus:
        if env not in _BINANCE_CRED_ENVS:
            raise HTTPException(status_code=422, detail=f"Invalid env. Must be one of: {list(_BINANCE_CRED_ENVS)}")
        api_key = str(body.get("api_key") or "").strip()
        api_secret = str(body.get("api_secret") or "").strip()
        if not api_key or not api_secret:
            raise HTTPException(status_code=422, detail="api_key and api_secret are required")

        base_url = _BINANCE_CRED_ENVS[env]

        from binance.client import BinanceHTTPClient
        test_client = BinanceHTTPClient(api_key=api_key, api_secret=api_secret, base_url=base_url, timeout=10.0)
        try:
            account_info = await test_client.fetch_account_info()
            if not account_info:
                raise HTTPException(status_code=400, detail="Binance API connection test failed: empty response")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Binance API connection test failed: {exc}") from exc
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
        )
        await session.commit()
        return BinanceCredentialStatus(env=env, configured=True, api_key_masked=_mask_key(api_key))

    @app.delete("/api/me/binance-keys/{env}")
    async def delete_binance_key(
        env: str,
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, bool]:
        if env not in _BINANCE_CRED_ENVS:
            raise HTTPException(status_code=422, detail=f"Invalid env. Must be one of: {list(_BINANCE_CRED_ENVS)}")
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
                raise HTTPException(status_code=400, detail="Upbit API connection test failed: unexpected response")
        except HTTPException:
            raise
        except UpbitClientError as exc:
            raise HTTPException(status_code=400, detail=f"Upbit API connection test failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Upbit API connection test failed: {exc}") from exc
        finally:
            await test_client.aclose()

        from common.crypto import get_crypto_service
        crypto = get_crypto_service()
        key_enc = crypto.encrypt(access_key)
        secret_enc = crypto.encrypt(secret_key)

        from control.repo import update_user_upbit_keys
        await update_user_upbit_keys(session, user_id=user.user_id, api_key_enc=key_enc, api_secret_enc=secret_enc)
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
        await update_user_upbit_keys(session, user_id=user.user_id, api_key_enc=None, api_secret_enc=None)
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
        from control.repo import get_user_profile
        from upbit.client import UpbitClient, UpbitClientError
        from common.crypto import get_crypto_service

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
        from control.repo import create_bridge_transfer, get_user_profile, update_bridge_transfer
        from upbit.client import UpbitClient, UpbitClientError
        from binance.earn_client import BinanceEarnClient, BinanceEarnClientError
        from common.crypto import get_crypto_service
        import uuid as _uuid

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
        binance_cred_onramp = await _get_binance_cred_onramp(session, user_id=user.user_id, env="mainnet")
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
        from control.repo import create_bridge_transfer, get_user_profile, update_bridge_transfer
        from upbit.client import UpbitClient, UpbitClientError
        from binance.earn_client import BinanceEarnClient, BinanceEarnClientError
        from common.crypto import get_crypto_service

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
        binance_cred_offramp = await _get_binance_cred_offramp(session, user_id=user.user_id, env="mainnet")
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
        from control.repo import get_bridge_transfer, get_user_profile, update_bridge_transfer
        from common.crypto import get_crypto_service

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
            from control.repo import get_binance_credential as _get_binance_cred
            from binance.earn_client import BinanceEarnClient

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
            for item in (deposits if isinstance(deposits, list) else []):
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
                    raise HTTPException(status_code=400, detail="Binance mainnet API keys not configured")
                try:
                    history = await binance.get_withdrawal_history(withdraw_order_id=transfer.src_withdrawal_id)
                    for item in history:
                        if str(item.get("id")) == transfer.src_withdrawal_id or str(item.get("withdrawOrderId")) == transfer.src_withdrawal_id:
                            status_code = int(item.get("status", -1))
                            if status_code == 6:  # Completed
                                new_status = "CONFIRMING"
                                update_kwargs["dst_txid"] = str(item.get("txId") or "")
                                update_kwargs["fee_usdt"] = float(item.get("transactionFee") or 0)
                            elif status_code in (1, 3, 5):  # Cancelled / Rejected / Failure
                                new_status = "FAILED"
                                update_kwargs["error_message"] = f"Binance withdrawal status={status_code}"
                            break
                finally:
                    await binance.aclose()

                # Destination side: check Upbit deposit history
                if new_status in ("CONFIRMING", "WITHDRAWING") and (update_kwargs.get("dst_txid") or transfer.dst_txid):
                    upbit = _get_upbit_client_for_user(profile, crypto)
                    try:
                        check_txid = update_kwargs.get("dst_txid") or transfer.dst_txid
                        dep = await upbit.get_deposit(check_txid)
                        if isinstance(dep, dict) and str(dep.get("state", "")).upper() == "ACCEPTED":
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
                update_kwargs["completed_at"] = datetime.now(timezone.utc)
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
        from control.repo import get_user_profile, get_binance_credential
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
        from binance.earn_client import BinanceEarnClient, BinanceEarnClientError
        from common.crypto import get_crypto_service
        from control.repo import get_binance_credential

        mainnet_cred = await get_binance_credential(session, user_id=user.user_id, env="mainnet")
        if not mainnet_cred:
            return WalletOverviewResponse(
                total_usdt=0.0,
                wallets=[],
                as_of=datetime.now(timezone.utc),
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
            as_of=datetime.now(timezone.utc),
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

        now = datetime.now(timezone.utc)

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

        unattributed = [
            p for p in all_positions if f"{p.symbol}-{p.side}" not in claimed
        ]

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
        price_map = {"pro": stripe_settings.price_id_pro, "enterprise": stripe_settings.price_id_enterprise}
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
            raise HTTPException(status_code=400, detail="No billing account found. Subscribe first.")

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
        from control.repo import get_user_profile, get_usage_count
        from api.plans import get_plan_limits

        profile = await get_user_profile(session, user_id=user.user_id)
        plan = profile.plan if profile else "free"
        limits = get_plan_limits(plan)

        from datetime import datetime as dt, timezone as tz
        period = dt.now(tz.utc).strftime("%Y-%m")
        bt_used = await get_usage_count(session, user_id=user.user_id, action="backtest", period_key=period)
        llm_used = await get_usage_count(session, user_id=user.user_id, action="llm_generate", period_key=period)

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
            "plan_expires_at": profile.plan_expires_at.isoformat() if profile and profile.plan_expires_at else None,
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
                    profile = await get_user_by_stripe_customer_id(session, stripe_customer_id=customer_id)
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
                    from control.repo import get_user_by_stripe_customer_id, update_user_plan
                    from datetime import datetime as dt, timedelta, timezone as tz
                    profile = await get_user_by_stripe_customer_id(session, stripe_customer_id=customer_id)
                    if profile:
                        grace = dt.now(tz.utc) + timedelta(days=3)
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
            select(UserProfile).where(
                UserProfile.email == normalized_email,
                UserProfile.email_verification_token == token,
            ).limit(1)
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

    return app


app = create_app()
