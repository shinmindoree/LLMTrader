from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
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
    get_job,
    list_events,
    list_jobs,
    list_orders,
    list_trades,
    request_stop,
    stop_all_jobs,
)
from settings import get_settings
from llm.client import LLMClient

from api.deps import require_admin
from api.schemas import (
    HealthResponse,
    JobCreateRequest,
    JobEventResponse,
    JobResponse,
    OrderResponse,
    StopResponse,
    StopAllResponse,
    StrategyInfo,
    StrategyGenerateRequest,
    StrategyGenerateResponse,
    StrategySaveRequest,
    StrategySaveResponse,
    TradeResponse,
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strategy_dirs() -> list[Path]:
    settings = get_settings()
    parts = [p.strip() for p in (settings.strategy_dirs or ".").split(",") if p.strip()]
    root = _repo_root()
    return [(root / p).resolve() for p in parts]


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
    engine = create_async_engine(settings.database_url)
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

    @app.on_event("startup")
    async def _startup() -> None:
        await init_db(engine)

    async def _db_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        try:
            async with session_maker() as session:
                await session.execute(text("SELECT 1"))
            return HealthResponse(status="ok", db_ok=True, db_error=None)
        except Exception as exc:  # noqa: BLE001
            return HealthResponse(status="error", db_ok=False, db_error=str(exc))

    @app.get("/api/strategies", response_model=list[StrategyInfo], dependencies=[Depends(require_admin)])
    async def strategies() -> list[StrategyInfo]:
        root = _repo_root()
        dirs = _strategy_dirs()
        files = list_strategy_files(dirs)
        out: list[StrategyInfo] = []
        for p in files:
            out.append(StrategyInfo(name=p.name, path=str(p.relative_to(root))))
        return out

    @app.post(
        "/api/strategies/generate",
        response_model=StrategyGenerateResponse,
        dependencies=[Depends(require_admin)],
    )
    async def generate_strategy(body: StrategyGenerateRequest) -> StrategyGenerateResponse:
        prompt = (body.user_prompt or "").strip()
        messages = body.messages
        if not messages and not prompt:
            raise HTTPException(status_code=422, detail="user_prompt must be non-empty")

        dirs = _strategy_dirs()
        if not dirs:
            raise HTTPException(status_code=500, detail="STRATEGY_DIRS is not configured")

        base_dir = dirs[0]
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Failed to prepare strategy dir: {exc}") from exc

        try:
            client = LLMClient()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        if messages:
            openai_messages = [{"role": m.role, "content": m.content} for m in messages]
            result = await client.generate_strategy("", messages=openai_messages)
        else:
            result = await client.generate_strategy(prompt)
        if not result.success or not result.code:
            raise HTTPException(status_code=502, detail=result.error or "LLM generation failed")

        code = _strip_code_fences(result.code)
        repo_root = _repo_root()
        temp_path = base_dir / f"_verify_{uuid.uuid4().hex}_strategy.py"

        try:
            temp_path.write_text(code, encoding="utf-8")
            _verify_strategy_load(temp_path, repo_root)
            _verify_strategy_backtest(temp_path, repo_root)
        except ValueError as exc:
            temp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"Strategy verification failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            temp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"Strategy verification failed: {exc}") from exc

        temp_path.unlink(missing_ok=True)
        summary = await client.summarize_strategy(code)
        return StrategyGenerateResponse(
            path=None,
            code=code,
            model_used=result.model_used,
            summary=summary,
            backtest_ok=True,
        )

    async def _generate_stream_events(body: StrategyGenerateRequest):
        prompt = (body.user_prompt or "").strip()
        messages = body.messages
        if not messages and not prompt:
            yield f"data: {json.dumps({'error': 'user_prompt must be non-empty'})}\n\n"
            return
        dirs = _strategy_dirs()
        if not dirs:
            yield f"data: {json.dumps({'error': 'STRATEGY_DIRS is not configured'})}\n\n"
            return
        try:
            client = LLMClient()
        except ValueError as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            return
        base_dir = dirs[0]
        repo_root = _repo_root()
        code_acc: list[str] = []
        try:
            if messages:
                openai_messages = [{"role": m.role, "content": m.content} for m in messages]
                stream = client.generate_strategy_stream("", messages=openai_messages)
            else:
                stream = client.generate_strategy_stream(prompt)
            async for event in stream:
                if "error" in event:
                    yield f"data: {json.dumps(event)}\n\n"
                    return
                if "token" in event:
                    code_acc.append(event["token"])
                    yield f"data: {json.dumps({'token': event['token']})}\n\n"
                if event.get("done"):
                    break
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            return
        code = _strip_code_fences("".join(code_acc))
        if not code:
            yield f"data: {json.dumps({'error': 'Empty code from stream'})}\n\n"
            return
        temp_path = base_dir / f"_verify_{uuid.uuid4().hex}_strategy.py"
        try:
            temp_path.write_text(code, encoding="utf-8")
            _verify_strategy_load(temp_path, repo_root)
            _verify_strategy_backtest(temp_path, repo_root)
        except ValueError as exc:
            temp_path.unlink(missing_ok=True)
            yield f"data: {json.dumps({'done': True, 'error': str(exc), 'code': code})}\n\n"
            return
        except Exception as exc:  # noqa: BLE001
            temp_path.unlink(missing_ok=True)
            yield f"data: {json.dumps({'done': True, 'error': str(exc), 'code': code})}\n\n"
            return
        temp_path.unlink(missing_ok=True)
        summary = await client.summarize_strategy(code)
        yield f"data: {json.dumps({'done': True, 'code': code, 'summary': summary, 'backtest_ok': True})}\n\n"

    @app.post("/api/strategies/generate/stream")
    async def generate_strategy_stream_endpoint(body: StrategyGenerateRequest):
        return StreamingResponse(
            _generate_stream_events(body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/strategies/save",
        response_model=StrategySaveResponse,
        dependencies=[Depends(require_admin)],
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
        temp_path = base_dir / f"_verify_{uuid.uuid4().hex}_strategy.py"

        try:
            temp_path.write_text(code, encoding="utf-8")
            _verify_strategy_load(temp_path, repo_root)
            _verify_strategy_backtest(temp_path, repo_root)
        except ValueError as exc:
            temp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"Strategy verification failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            temp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"Strategy verification failed: {exc}") from exc

        try:
            final_target.write_text(code, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            temp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Failed to write strategy file: {exc}") from exc
        temp_path.unlink(missing_ok=True)

        relative_path = str(final_target.relative_to(repo_root))
        return StrategySaveResponse(path=relative_path)

    @app.post("/api/jobs", response_model=JobResponse, dependencies=[Depends(require_admin)])
    async def create_job_api(
        body: JobCreateRequest,
        session: AsyncSession = Depends(_db_session),
    ) -> JobResponse:
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

        # MVP safety: enforce single LIVE run at a time.
        if body.type == JobType.LIVE:
            res = await session.execute(
                select(Job.job_id)
                .where(Job.type == str(JobType.LIVE))
                .where(Job.started_at.is_not(None))
                .where(Job.status.in_([JobStatus.RUNNING, JobStatus.STOP_REQUESTED]))
                .limit(1)
            )
            active_job_id = res.scalar_one_or_none()
            if active_job_id is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "A LIVE job is already running (or stopping). Stop it before starting a new one.",
                        "active_job_id": str(active_job_id),
                    },
                )

        job = await create_job(
            session,
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
        await session.commit()
        return _job_to_response(job)

    @app.get("/api/jobs", response_model=list[JobResponse], dependencies=[Depends(require_admin)])
    async def jobs(
        limit: int = Query(default=50, ge=1, le=200),
        job_type: JobType | None = Query(default=None, alias="type"),
        session: AsyncSession = Depends(_db_session),
    ) -> list[JobResponse]:
        rows = await list_jobs(session, limit=limit, job_type=job_type)
        return [_job_to_response(j) for j in rows]

    @app.post("/api/jobs/stop-all", response_model=StopAllResponse, dependencies=[Depends(require_admin)])
    async def stop_all(
        job_type: JobType | None = Query(default=None, alias="type"),
        session: AsyncSession = Depends(_db_session),
    ) -> StopAllResponse:
        # NOTE: This route must be registered before `/api/jobs/{job_id}`.
        # Starlette uses first-match semantics and will otherwise treat `{job_id}` as a partial match
        # and return `405 Method Not Allowed` for POST.
        counts = await stop_all_jobs(session, job_type=job_type)
        await session.commit()
        return StopAllResponse(**counts)

    @app.get("/api/jobs/{job_id}", response_model=JobResponse, dependencies=[Depends(require_admin)])
    async def job_detail(job_id: uuid.UUID, session: AsyncSession = Depends(_db_session)) -> JobResponse:
        job = await get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Not found")
        return _job_to_response(job)

    @app.post("/api/jobs/{job_id}/stop", response_model=StopResponse, dependencies=[Depends(require_admin)])
    async def stop_job(job_id: uuid.UUID, session: AsyncSession = Depends(_db_session)) -> StopResponse:
        new_status = await request_stop(session, job_id)
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

    @app.get("/api/jobs/{job_id}/events", response_model=list[JobEventResponse], dependencies=[Depends(require_admin)])
    async def events(
        job_id: uuid.UUID,
        after_event_id: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
        session: AsyncSession = Depends(_db_session),
    ) -> list[JobEventResponse]:
        rows = await list_events(session, job_id=job_id, after_event_id=after_event_id, limit=limit)
        return [_event_to_response(e) for e in rows]

    @app.get(
        "/api/jobs/{job_id}/events/stream",
        response_class=StreamingResponse,
        dependencies=[Depends(require_admin)],
    )
    async def events_stream(
        job_id: uuid.UUID,
        after_event_id: int = Query(default=0, ge=0),
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

    @app.get("/api/jobs/{job_id}/orders", response_model=list[OrderResponse], dependencies=[Depends(require_admin)])
    async def orders(job_id: uuid.UUID, session: AsyncSession = Depends(_db_session)) -> list[OrderResponse]:
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

    @app.get("/api/jobs/{job_id}/trades", response_model=list[TradeResponse], dependencies=[Depends(require_admin)])
    async def trades(job_id: uuid.UUID, session: AsyncSession = Depends(_db_session)) -> list[TradeResponse]:
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

    return app


app = create_app()
