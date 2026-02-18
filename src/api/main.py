from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
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

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from control.db import create_async_engine, create_session_maker, init_db
from control.enums import EventKind, JobStatus, JobType
from control.models import Job
from control.repo import (
    append_event,
    create_job,
    create_strategy_quality_log,
    delete_strategy_chat_session as repo_delete_strategy_chat_session,
    delete_job,
    delete_jobs,
    get_account_snapshot,
    get_job,
    list_events,
    list_jobs,
    list_orders,
    list_strategy_chat_sessions as repo_list_strategy_chat_sessions,
    list_strategy_quality_logs,
    list_trades,
    request_stop,
    stop_all_jobs,
    upsert_strategy_chat_session as repo_upsert_strategy_chat_session,
)
from settings import get_settings
from llm.client import LLMClient
try:
    from relay.capability_registry import (
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

from api.deps import AuthenticatedUser, require_auth, set_session_maker
from api.job_policy import evaluate_job_policy
from api.schemas import (
    HealthResponse,
    CountItem,
    DeleteAllResponse,
    DeleteResponse,
    JobPolicyCheckRequest,
    JobPolicyCheckResponse,
    JobCreateRequest,
    JobEventResponse,
    JobResponse,
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
    StrategyChatSessionUpsertRequest,
    StrategyChatRequest,
    StrategyChatResponse,
    StrategySyntaxCheckRequest,
    StrategySyntaxCheckResponse,
    StrategySyntaxError,
    StrategySaveRequest,
    StrategySaveResponse,
    TradeResponse,
    BinanceAssetBalance,
    BinancePositionSummary,
    BinanceAccountSummaryResponse,
)
from api.strategy_catalog import list_strategy_files, validate_strategy_path


def _job_to_response(job: Any) -> JobResponse:
    return JobResponse(
        job_id=job.job_id,
        type=JobType(str(job.type)),
        status=job.status,
        strategy_path=job.strategy_path,
        config=job.config_json,
        result=job.result_json,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        ended_at=job.ended_at,
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

    app = FastAPI(title="LLMTrader API", version="0.1.0")
    app.state.engine = engine
    app.state.session_maker = session_maker

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    set_session_maker(session_maker)

    @app.on_event("startup")
    async def _startup() -> None:
        await init_db(engine)

    async def _db_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

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

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        try:
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
        if not profile or not profile.binance_api_key_enc or not profile.binance_api_secret_enc:
            return BinanceAccountSummaryResponse(
                configured=False,
                connected=False,
                mode="testnet",
                base_url="",
                error="Binance API keys are not configured. Go to Settings to set up your keys.",
            )

        from common.crypto import get_crypto_service
        try:
            crypto = get_crypto_service()
            api_key = crypto.decrypt(profile.binance_api_key_enc)
            api_secret = crypto.decrypt(profile.binance_api_secret_enc)
        except Exception as exc:  # noqa: BLE001
            return BinanceAccountSummaryResponse(
                configured=True,
                connected=False,
                mode="testnet",
                base_url=profile.binance_base_url,
                error=f"Failed to decrypt keys: {type(exc).__name__}",
            )

        base_url = profile.binance_base_url or "https://testnet.binancefuture.com"

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

    @app.get("/api/strategies", response_model=list[StrategyInfo], dependencies=[Depends(require_auth)])
    async def strategies() -> list[StrategyInfo]:
        root = _repo_root()
        dirs = _strategy_dirs()
        files = list_strategy_files(dirs)
        out: list[StrategyInfo] = []
        for p in files:
            out.append(StrategyInfo(name=p.name, path=str(p.relative_to(root))))
        return out

    @app.get(
        "/api/strategies/content",
        response_model=StrategyContentResponse,
        dependencies=[Depends(require_auth)],
    )
    async def strategy_content(path: str = Query(..., alias="path")) -> StrategyContentResponse:
        root = _repo_root()
        dirs = _strategy_dirs()
        try:
            target = validate_strategy_path(
                repo_root=root,
                strategy_dirs=dirs,
                strategy_path=path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            code = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read strategy file: {exc}") from exc

        return StrategyContentResponse(
            name=target.name,
            path=str(target.relative_to(root)),
            code=code,
        )

    @app.delete(
        "/api/strategies",
        response_model=DeleteResponse,
        dependencies=[Depends(require_auth)],
    )
    async def delete_strategy(path: str = Query(..., alias="path")) -> DeleteResponse:
        root = _repo_root()
        dirs = _strategy_dirs()
        try:
            target = validate_strategy_path(
                repo_root=root,
                strategy_dirs=dirs,
                strategy_path=path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            target.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete file: {exc}") from exc
        return DeleteResponse(ok=True)

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

    @app.get(
        "/api/strategies/capabilities",
        response_model=StrategyCapabilityResponse,
        dependencies=[Depends(require_auth)],
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
        dependencies=[Depends(require_auth)],
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
        try:
            if messages:
                stream = client.generate_strategy_stream("", messages=openai_messages)
            else:
                stream = client.generate_strategy_stream(prompt)
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
                if "token" in event:
                    code_acc.append(event["token"])
                    yield f"data: {json.dumps({'token': event['token']})}\n\n"
                if event.get("done"):
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
            repaired=False,
            repair_attempts=0,
        )
        yield (
            "data: "
            + json.dumps(
                {
                    "done": True,
                    "code": code,
                    "summary": None,
                    "backtest_ok": False,
                    "repaired": False,
                    "repair_attempts": 0,
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
        if not code:
            raise HTTPException(status_code=422, detail="code must be non-empty")
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
    async def save_strategy(body: StrategySaveRequest) -> StrategySaveResponse:
        code = (body.code or "").strip()
        if not code:
            raise HTTPException(status_code=422, detail="code must be non-empty")

        dirs = _strategy_dirs()
        if not dirs:
            raise HTTPException(status_code=500, detail="STRATEGY_DIRS is not configured")

        base_dir = dirs[0]
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Failed to prepare strategy dir: {exc}") from exc

        code = _strip_code_fences(code)
        filename = _sanitize_strategy_filename(body.strategy_name)
        final_target = _unique_strategy_path(base_dir, filename)
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

        try:
            final_target.write_text(code, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            _cleanup_verify_temp(temp_path)
            raise HTTPException(status_code=500, detail=f"Failed to write strategy file: {exc}") from exc
        _cleanup_verify_temp(temp_path)

        relative_path = str(final_target.relative_to(repo_root))
        return StrategySaveResponse(path=relative_path)

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

        root = _repo_root()
        dirs = _strategy_dirs()
        try:
            validated = validate_strategy_path(
                repo_root=root,
                strategy_dirs=dirs,
                strategy_path=body.strategy_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        from control.repo import count_active_jobs
        if body.type == JobType.LIVE:
            active_count = await count_active_jobs(session, user_id=user.user_id, job_type=JobType.LIVE)
            if active_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "A LIVE job is already running (or stopping). Stop it before starting a new one.",
                    },
                )

        job = await create_job(
            session,
            user_id=user.user_id,
            job_type=body.type,
            strategy_path=str(validated.relative_to(root)),
            config_json=body.config,
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
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> list[JobResponse]:
        rows = await list_jobs(session, user_id=user.user_id, limit=limit, job_type=job_type)
        return [_job_to_response(j) for j in rows]

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
        from control.repo import get_user_profile
        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        has_binance_keys = bool(profile.binance_api_key_enc)
        return {
            "user_id": profile.user_id,
            "email": profile.email,
            "display_name": profile.display_name,
            "plan": profile.plan,
            "has_binance_keys": has_binance_keys,
            "binance_base_url": profile.binance_base_url,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
        }

    def _mask_key(key: str) -> str:
        if len(key) <= 8:
            return "***"
        return key[:4] + "***" + key[-4:]

    @app.put("/api/me/binance-keys")
    async def set_binance_keys(
        body: dict[str, Any],
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        api_key = str(body.get("api_key") or "").strip()
        api_secret = str(body.get("api_secret") or "").strip()
        base_url = str(body.get("base_url") or "https://testnet.binancefuture.com").strip()
        if not api_key or not api_secret:
            raise HTTPException(status_code=422, detail="api_key and api_secret are required")

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

        from control.repo import update_user_binance_keys
        await update_user_binance_keys(
            session,
            user_id=user.user_id,
            api_key_enc=api_key_enc,
            api_secret_enc=api_secret_enc,
            base_url=base_url,
        )
        await session.commit()
        return {"ok": True, "api_key_masked": _mask_key(api_key), "base_url": base_url}

    @app.get("/api/me/binance-keys")
    async def get_binance_keys(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, Any]:
        from control.repo import get_user_profile
        profile = await get_user_profile(session, user_id=user.user_id)
        if not profile or not profile.binance_api_key_enc:
            return {"configured": False}

        from common.crypto import get_crypto_service
        crypto = get_crypto_service()
        try:
            raw_key = crypto.decrypt(profile.binance_api_key_enc)
        except Exception:  # noqa: BLE001
            return {"configured": True, "api_key_masked": "***decryption_error***", "base_url": profile.binance_base_url}

        return {
            "configured": True,
            "api_key_masked": _mask_key(raw_key),
            "base_url": profile.binance_base_url,
        }

    @app.delete("/api/me/binance-keys")
    async def delete_binance_keys(
        user: AuthenticatedUser = Depends(require_auth),
        session: AsyncSession = Depends(_db_session),
    ) -> dict[str, bool]:
        from control.repo import update_user_binance_keys
        await update_user_binance_keys(
            session,
            user_id=user.user_id,
            api_key_enc=None,
            api_secret_enc=None,
            base_url="https://testnet.binancefuture.com",
        )
        await session.commit()
        return {"ok": True}

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

    return app


app = create_app()
