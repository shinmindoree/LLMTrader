"""FastAPI app for LLM relay (Azure OpenAI proxy)."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from relay.auth import require_api_key
from relay.azure_openai import chat_completion, chat_completion_messages, chat_completion_stream
from relay.capability_registry import (
    SUPPORTED_CONTEXT_METHODS,
    SUPPORTED_DATA_SOURCES,
    SUPPORTED_INDICATOR_SCOPES,
    UNSUPPORTED_CAPABILITY_RULES,
    build_development_requirements,
    capability_summary_lines,
    detect_unsupported_requirements,
)
from relay.config import get_config
from relay.prompts import build_intake_system_prompt, build_repair_system_prompt, build_system_prompt


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


class IntakeResponse(BaseModel):
    intent: str
    status: str
    user_message: str
    normalized_spec: dict[str, Any] | None = None
    missing_fields: list[str]
    unsupported_requirements: list[str]
    clarification_questions: list[str]
    assumptions: list[str]
    development_requirements: list[str]


class RepairRequest(BaseModel):
    code: str
    verification_error: str
    user_prompt: str | None = None
    messages: list[ChatMessage] | None = None


class RepairResponse(BaseModel):
    code: str
    model_used: str | None = None


class CapabilityResponse(BaseModel):
    supported_data_sources: list[str]
    supported_indicator_scopes: list[str]
    supported_context_methods: list[str]
    unsupported_categories: list[str]
    summary_lines: list[str]


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


_ALLOWED_INTENTS = {
    "OUT_OF_SCOPE",
    "STRATEGY_CREATE",
    "STRATEGY_MODIFY",
    "STRATEGY_QA",
}
_ALLOWED_STATUSES = {
    "READY",
    "NEEDS_CLARIFICATION",
    "UNSUPPORTED_CAPABILITY",
    "OUT_OF_SCOPE",
}
_GENERIC_REQUEST_PATTERNS = [
    r"^전략\s*생성",
    r"^전략\s*만들",
    r"전략.*아무거나",
    r"아무거나.*전략",
    r"알아서.*전략",
    r"strategy\s*(please|generate|create)?\s*$",
]
_MISSING_FIELD_ORDER = ("symbol", "timeframe", "entry_logic", "exit_logic", "risk")
_MISSING_FIELD_QUESTIONS = {
    "symbol": "어떤 심볼로 거래할까요? (예: BTCUSDT)",
    "timeframe": "어떤 캔들 간격을 사용할까요? (예: 1m, 15m, 1h, 4h)",
    "entry_logic": "진입 조건을 한 줄로 구체적으로 적어주세요.",
    "exit_logic": "청산 조건을 한 줄로 구체적으로 적어주세요.",
    "risk": "리스크 관리는 어떻게 할까요? (예: 고정 수량, 계좌 비율, 손절 기준)",
}
_FIELD_TO_QUESTION_CATEGORY = {
    "symbol": "symbol",
    "timeframe": "timeframe",
    "entry_logic": "entry",
    "exit_logic": "exit",
    "risk": "risk",
}
_QUESTION_CATEGORY_KEYWORDS = {
    "symbol": (
        "symbol",
        "ticker",
        "market",
        "pair",
        "심볼",
        "종목",
        "티커",
        "거래쌍",
        "자산",
    ),
    "timeframe": (
        "timeframe",
        "interval",
        "candle",
        "timescale",
        "타임프레임",
        "캔들",
        "캔들간격",
        "봉",
        "시간간격",
    ),
    "entry": (
        "entry",
        "enter",
        "buycondition",
        "진입",
        "매수조건",
        "롱조건",
        "숏조건",
    ),
    "risk": (
        "risk",
        "position",
        "size",
        "leverage",
        "drawdown",
        "리스크",
        "위험관리",
        "수량",
        "비중",
        "레버리지",
        "손실한도",
    ),
    "exit": (
        "exit",
        "close",
        "takeprofit",
        "stoploss",
        "청산",
        "익절",
        "손절",
        "종료",
    ),
}


def _extract_json_object(content: str) -> dict[str, Any] | None:
    text = (content or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _to_str_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        val = str(item).strip()
        if val:
            out.append(val)
    return out


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _normalize_text(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (value or "").lower())


def _question_category(question: str) -> str | None:
    normalized = _normalize_text(question)
    if not normalized:
        return None
    for category, keywords in _QUESTION_CATEGORY_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return category
    return None


def _merge_clarification_questions(
    missing_fields: list[str],
    model_questions: list[str],
) -> list[str]:
    ordered_missing = [f for f in _MISSING_FIELD_ORDER if f in missing_fields]
    ordered_missing.extend(f for f in missing_fields if f not in ordered_missing)

    result: list[str] = []
    seen_text: set[str] = set()
    seen_categories: set[str] = set()

    def _push(question: str, category: str | None = None) -> None:
        q = question.strip()
        if not q:
            return
        normalized = _normalize_text(q)
        if not normalized or normalized in seen_text:
            return
        cat = category or _question_category(q)
        if cat and cat in seen_categories:
            return
        seen_text.add(normalized)
        if cat:
            seen_categories.add(cat)
        result.append(q)

    for field in ordered_missing:
        default_q = _MISSING_FIELD_QUESTIONS.get(field)
        if default_q:
            _push(default_q, _FIELD_TO_QUESTION_CATEGORY.get(field))

    for question in model_questions:
        _push(question)

    return result


def _is_generic_strategy_prompt(prompt: str) -> bool:
    text = (prompt or "").strip().lower()
    if not text:
        return False
    return any(re.search(pattern, text) for pattern in _GENERIC_REQUEST_PATTERNS)


def _sanitize_intake_response(
    payload: dict[str, Any],
    *,
    prompt: str,
    messages: list[ChatMessage],
) -> IntakeResponse:
    intent = str(payload.get("intent") or "STRATEGY_CREATE").strip().upper()
    if intent not in _ALLOWED_INTENTS:
        intent = "STRATEGY_CREATE"

    status = str(payload.get("status") or "NEEDS_CLARIFICATION").strip().upper()
    if status not in _ALLOWED_STATUSES:
        status = "NEEDS_CLARIFICATION"

    spec_raw = payload.get("normalized_spec")
    normalized_spec: dict[str, Any] = {}
    if isinstance(spec_raw, dict):
        normalized_spec = {
            "symbol": (str(spec_raw.get("symbol")).strip() or None) if spec_raw.get("symbol") is not None else None,
            "timeframe": (str(spec_raw.get("timeframe")).strip() or None)
            if spec_raw.get("timeframe") is not None
            else None,
            "entry_logic": (str(spec_raw.get("entry_logic")).strip() or None)
            if spec_raw.get("entry_logic") is not None
            else None,
            "exit_logic": (str(spec_raw.get("exit_logic")).strip() or None)
            if spec_raw.get("exit_logic") is not None
            else None,
            "risk": spec_raw.get("risk") if isinstance(spec_raw.get("risk"), dict) else {},
        }
    else:
        normalized_spec = {
            "symbol": None,
            "timeframe": None,
            "entry_logic": None,
            "exit_logic": None,
            "risk": {},
        }

    missing_fields = _unique_preserve_order(_to_str_list(payload.get("missing_fields")))
    unsupported = _unique_preserve_order(_to_str_list(payload.get("unsupported_requirements")))
    clarification_questions = _to_str_list(payload.get("clarification_questions"))
    assumptions = _unique_preserve_order(_to_str_list(payload.get("assumptions")))
    development_requirements = _unique_preserve_order(_to_str_list(payload.get("development_requirements")))

    conversation_text = " ".join(
        [prompt] + [str(m.content or "") for m in messages]
    ).lower()
    for note in detect_unsupported_requirements(conversation_text):
        if note not in unsupported:
            unsupported.append(note)
    for line in build_development_requirements(conversation_text):
        if line not in development_requirements:
            development_requirements.append(line)

    if intent == "STRATEGY_CREATE":
        if not normalized_spec.get("entry_logic") and "entry_logic" not in missing_fields:
            missing_fields.append("entry_logic")
        if not normalized_spec.get("exit_logic") and "exit_logic" not in missing_fields:
            missing_fields.append("exit_logic")
        if _is_generic_strategy_prompt(prompt):
            for field in ("symbol", "timeframe", "entry_logic", "exit_logic"):
                if field not in missing_fields:
                    missing_fields.append(field)

    clarification_questions = _merge_clarification_questions(
        missing_fields=missing_fields,
        model_questions=clarification_questions,
    )

    if intent == "OUT_OF_SCOPE":
        status = "OUT_OF_SCOPE"
    elif unsupported:
        status = "UNSUPPORTED_CAPABILITY"
    elif missing_fields:
        status = "NEEDS_CLARIFICATION"
    elif status != "READY":
        status = "NEEDS_CLARIFICATION"

    user_message = str(payload.get("user_message") or "").strip()
    if not user_message:
        if status == "OUT_OF_SCOPE":
            user_message = "이 입력은 트레이딩 전략 생성 요청으로 보기 어렵습니다. 전략 설명으로 다시 입력해주세요."
        elif status == "UNSUPPORTED_CAPABILITY":
            user_message = "요청에는 현재 시스템에 없는 외부 연동 기능이 필요합니다."
        elif status == "NEEDS_CLARIFICATION":
            user_message = "전략 생성 전에 몇 가지 정보가 더 필요합니다."
        else:
            user_message = "요청이 명확하여 전략 생성을 진행할 수 있습니다."
    if status == "UNSUPPORTED_CAPABILITY":
        for line in capability_summary_lines():
            if line not in assumptions:
                assumptions.append(line)

    return IntakeResponse(
        intent=intent,
        status=status,
        user_message=user_message,
        normalized_spec=normalized_spec,
        missing_fields=missing_fields,
        unsupported_requirements=unsupported,
        clarification_questions=clarification_questions,
        assumptions=assumptions,
        development_requirements=development_requirements,
    )


def _strategy_chat_system_prompt(code: str, summary: str | None) -> str:
    return (
        "You are a trading strategy assistant. The user has the following strategy. "
        "Answer their questions in natural language. Do not generate new code. "
        "Use Korean if the user writes in Korean. "
        "If the user asks for more detail, continue from the prior summary and expand it step-by-step "
        "(strategy overview -> entry flow -> exit flow -> risk/position sizing -> practical cautions) "
        "instead of restarting from scratch.\n\n"
        "Strategy code:\n"
        f"{code}\n\n"
        f"Summary:\n{summary or 'N/A'}"
    )


@app.get("/capabilities", response_model=CapabilityResponse)
async def capabilities(
    _: None = Depends(require_api_key),
) -> CapabilityResponse:
    return CapabilityResponse(
        supported_data_sources=list(SUPPORTED_DATA_SOURCES),
        supported_indicator_scopes=list(SUPPORTED_INDICATOR_SCOPES),
        supported_context_methods=list(SUPPORTED_CONTEXT_METHODS),
        unsupported_categories=[r.name for r in UNSUPPORTED_CAPABILITY_RULES],
        summary_lines=capability_summary_lines(),
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


@app.post("/intake", response_model=IntakeResponse)
async def intake(
    body: StrategyRequest,
    _: None = Depends(require_api_key),
) -> IntakeResponse:
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
        system_content = build_intake_system_prompt()
        if messages:
            openai_messages = [{"role": m.role, "content": m.content} for m in messages]
            content, _ = chat_completion_messages(
                config,
                system_content=system_content,
                messages=openai_messages,
            )
        else:
            content, _ = chat_completion(
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

    payload = _extract_json_object(content) or {}
    return _sanitize_intake_response(payload, prompt=prompt, messages=messages)


@app.post("/repair", response_model=RepairResponse)
async def repair(
    body: RepairRequest,
    _: None = Depends(require_api_key),
) -> RepairResponse:
    code = (body.code or "").strip()
    verification_error = (body.verification_error or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="code must be non-empty")
    if not verification_error:
        raise HTTPException(status_code=422, detail="verification_error must be non-empty")

    config = get_config()
    if not config.is_azure_configured():
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI not configured (missing env vars)",
        )

    prompt_parts = [
        "Fix the strategy code based on the verification failure below.",
        "",
        f"Verification error:\n{verification_error}",
    ]
    if body.user_prompt and body.user_prompt.strip():
        prompt_parts.extend(["", f"Original user request:\n{body.user_prompt.strip()}"])
    if body.messages:
        compact_msgs = [
            {"role": m.role, "content": m.content}
            for m in body.messages
            if str(m.content or "").strip()
        ]
        if compact_msgs:
            prompt_parts.extend(
                [
                    "",
                    "Conversation context (JSON):",
                    json.dumps(compact_msgs, ensure_ascii=False),
                ]
            )
    prompt_parts.extend(["", "Current code:", code])
    repair_user_content = "\n".join(prompt_parts)

    try:
        content, model_used = chat_completion(
            config,
            system_content=build_repair_system_prompt(),
            user_content=repair_user_content,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Azure OpenAI call failed: {e!s}",
        ) from e

    if not content or not content.strip():
        raise HTTPException(status_code=502, detail="Empty completion from model")

    return RepairResponse(code=content.strip(), model_used=model_used)


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
