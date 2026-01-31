"""FastAPI app for LLM relay (Azure OpenAI proxy)."""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from relay.auth import require_api_key
from relay.azure_openai import chat_completion, chat_completion_messages, chat_completion_stream
from relay.config import get_config
from relay.prompts import build_system_prompt


app = FastAPI(title="LLMTrader Relay", version="0.1.0")


class ChatMessage(BaseModel):
    role: str
    content: str


class StrategyRequest(BaseModel):
    user_prompt: str
    messages: list[ChatMessage] | None = None


class StrategyResponse(BaseModel):
    code: str
    model_used: str | None = None


class SummarizeRequest(BaseModel):
    code: str


class SummarizeResponse(BaseModel):
    summary: str


SUMMARY_SYSTEM_PROMPT = """You summarize trading strategy Python code in Korean.
Output only a short summary: 1) overall strategy in 2-3 lines, 2) entry conditions in one line, 3) exit conditions in one line.
No code, no markdown."""


class StrategyChatRequest(BaseModel):
    code: str
    summary: str | None = None
    messages: list[ChatMessage]


class StrategyChatResponse(BaseModel):
    content: str


def _strategy_chat_system_prompt(code: str, summary: str | None) -> str:
    return (
        "You are a trading strategy assistant. The user has the following strategy. "
        "Answer their questions in natural language. Do not generate new code. "
        "Use Korean if the user writes in Korean.\n\n"
        "Strategy code:\n"
        f"{code}\n\n"
        f"Summary:\n{summary or 'N/A'}"
    )


@app.post("/strategy/chat", response_model=StrategyChatResponse)
async def strategy_chat(
    body: StrategyChatRequest,
    _: None = Depends(require_api_key),
) -> StrategyChatResponse:
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="code must be non-empty")
    messages = body.messages or []
    if not messages:
        raise HTTPException(status_code=422, detail="messages must be non-empty")

    config = get_config()
    if not config.is_azure_configured():
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI not configured (missing env vars)",
        )

    try:
        system_content = _strategy_chat_system_prompt(code, body.summary)
        openai_messages = [{"role": m.role, "content": m.content} for m in messages]
        content, _ = chat_completion_messages(
            config,
            system_content=system_content,
            messages=openai_messages,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Azure OpenAI call failed: {e!s}",
        ) from e

    if not content or not content.strip():
        raise HTTPException(status_code=502, detail="Empty completion from model")

    return StrategyChatResponse(content=content.strip())


@app.post("/summarize", response_model=SummarizeResponse)
async def summarize(
    body: SummarizeRequest,
    _: None = Depends(require_api_key),
) -> SummarizeResponse:
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="code must be non-empty")

    config = get_config()
    if not config.is_azure_configured():
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI not configured (missing env vars)",
        )

    try:
        content, _ = chat_completion(
            config,
            system_content=SUMMARY_SYSTEM_PROMPT,
            user_content=code,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Azure OpenAI call failed: {e!s}",
        ) from e

    if not content or not content.strip():
        raise HTTPException(status_code=502, detail="Empty completion from model")

    return SummarizeResponse(summary=content.strip())


@app.post("/generate", response_model=StrategyResponse)
async def generate(
    body: StrategyRequest,
    _: None = Depends(require_api_key),
) -> StrategyResponse:
    prompt = (body.user_prompt or "").strip()
    messages = body.messages or []
    if not messages and not prompt:
        raise HTTPException(status_code=422, detail="user_prompt must be non-empty")

    config = get_config()
    if not config.is_azure_configured():
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI not configured (missing env vars)",
        )

    try:
        system_content = build_system_prompt()
        if messages:
            openai_messages = [{"role": m.role, "content": m.content} for m in messages]
            content, model_used = chat_completion_messages(
                config,
                system_content=system_content,
                messages=openai_messages,
            )
        else:
            content, model_used = chat_completion(
                config,
                system_content=system_content,
                user_content=prompt,
            )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Azure OpenAI call failed: {e!s}",
        ) from e

    if not content or not content.strip():
        raise HTTPException(status_code=502, detail="Empty completion from model")

    return StrategyResponse(code=content.strip(), model_used=model_used)


async def _generate_stream_body(body: StrategyRequest):
    prompt = (body.user_prompt or "").strip()
    messages = body.messages or []
    if not messages and not prompt:
        yield f"data: {json.dumps({'error': 'user_prompt must be non-empty'})}\n\n"
        return
    config = get_config()
    if not config.is_azure_configured():
        yield f"data: {json.dumps({'error': 'Azure OpenAI not configured'})}\n\n"
        return
    try:
        system_content = build_system_prompt()
        if messages:
            openai_messages = [{"role": m.role, "content": m.content} for m in messages]
            async for token in chat_completion_stream(
                config,
                system_content=system_content,
                messages=openai_messages,
            ):
                yield f"data: {json.dumps({'token': token})}\n\n"
        else:
            async for token in chat_completion_stream(
                config,
                system_content=system_content,
                user_content=prompt,
            ):
                yield f"data: {json.dumps({'token': token})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


@app.post("/generate/stream")
async def generate_stream(
    body: StrategyRequest,
    _: None = Depends(require_api_key),
):
    return StreamingResponse(
        _generate_stream_body(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
