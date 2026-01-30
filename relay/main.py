"""FastAPI app for LLM relay (Azure OpenAI proxy)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from relay.auth import require_api_key
from relay.azure_openai import chat_completion
from relay.config import get_config
from relay.prompts import build_system_prompt


app = FastAPI(title="LLMTrader Relay", version="0.1.0")


class StrategyRequest(BaseModel):
    user_prompt: str


class StrategyResponse(BaseModel):
    code: str
    model_used: str | None = None


@app.post("/generate", response_model=StrategyResponse)
async def generate(
    body: StrategyRequest,
    _: None = Depends(require_api_key),
) -> StrategyResponse:
    prompt = (body.user_prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="user_prompt must be non-empty")

    config = get_config()
    if not config.is_azure_configured():
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI not configured (missing env vars)",
        )

    try:
        system_content = build_system_prompt()
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
