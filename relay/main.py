"""FastAPI app for LLM relay (Azure OpenAI proxy)."""

from __future__ import annotations

import ast
import json
import logging
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
from relay.prompts import (
    SUMMARY_SYSTEM_PROMPT,
    TEST_SYSTEM_PROMPT,
    build_analyst_system_prompt,
    build_intake_system_prompt,
    build_planner_system_prompt,
    build_repair_system_prompt,
    build_strategy_chat_system_prompt,
    build_system_prompt,
)


app = FastAPI(title="LLMTrader Relay", version="0.1.0")
logger = logging.getLogger(__name__)


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


class TestRequest(BaseModel):
    input: str = "Hello"


class TestResponse(BaseModel):
    output: str


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


class AnalyzeRequest(BaseModel):
    code: str
    backtest_results: str
    summary: str | None = None


class AnalyzeResponse(BaseModel):
    analysis: dict[str, Any]


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

_NON_TRADING_REJECTION_MSG = (
    "죄송합니다. 이 요청은 트레이딩 전략과 관련이 없어 처리할 수 없습니다. "
    "트레이딩 전략에 대해 설명해 주시면 도움드리겠습니다! 😊"
)

_MODEL_REFUSAL_PATTERNS = [
    re.compile(r"I'?m sorry,?\s*but I cannot assist", re.IGNORECASE),
    re.compile(r"I cannot assist with th(at|is) request", re.IGNORECASE),
    re.compile(r"I'?m not able to (help|assist) with th(at|is)", re.IGNORECASE),
    re.compile(r"I can'?t (help|assist) with th(at|is)", re.IGNORECASE),
    re.compile(r"I'?m unable to (provide|generate|create|assist)", re.IGNORECASE),
    re.compile(r"as an AI,?\s*I (cannot|can't|am unable)", re.IGNORECASE),
]


def _is_model_refusal(text: str) -> bool:
    """Detect model-level safety refusal messages."""
    stripped = text.strip()
    if not stripped:
        return False
    return any(p.search(stripped) for p in _MODEL_REFUSAL_PATTERNS)
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

    if intent == "STRATEGY_CREATE" and _is_generic_strategy_prompt(prompt) and not assumptions:
        if not normalized_spec.get("entry_logic") and "entry_logic" not in missing_fields:
            missing_fields.append("entry_logic")

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


def _raise_llm_http_error(endpoint: str, exc: Exception) -> None:
    logger.exception("LLM call failed at %s: %s", endpoint, exc)
    raise HTTPException(
        status_code=502,
        detail=f"Azure OpenAI call failed: {exc!s}",
    ) from exc


_MAX_REPAIR_ATTEMPTS = 3


def _extract_python_code(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    start = 1
    if start < len(lines) and lines[start].strip().lower() in ("python", "py", "python3"):
        start += 1
    end = len(lines)
    for i in range(len(lines) - 1, 0, -1):
        if lines[i].strip() == "```":
            end = i
            break
    return "\n".join(lines[start:end]).strip()


def _sanitize_code_quotes(code: str) -> str:
    """Replace smart/curly quotes with ASCII equivalents to prevent SyntaxError."""
    return (
        code
        .replace("\u201c", '"')   # "
        .replace("\u201d", '"')   # "
        .replace("\u2018", "'")   # '
        .replace("\u2019", "'")   # '
    )


def _verify_strategy_code(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError at line {e.lineno}: {e.msg}"

    strategy_cls: ast.ClassDef | None = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name.endswith("Strategy") and node.name != "Strategy":
            strategy_cls = node
            break

    if strategy_cls is None:
        return "No class ending with 'Strategy' found."

    methods = {
        n.name
        for n in ast.walk(strategy_cls)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = [m for m in ("initialize", "on_bar") if m not in methods]
    if missing:
        return f"Class '{strategy_cls.name}' is missing required methods: {', '.join(missing)}"

    if "get_open_orders" not in code:
        return "Missing open-orders guard (ctx.get_open_orders)."
    if "is_new_bar" not in code:
        return "Missing bar-confirmation guard (is_new_bar check)."

    return _verify_strategy_quality(code, tree, strategy_cls)


def _verify_strategy_quality(
    code: str, tree: ast.Module, strategy_cls: ast.ClassDef
) -> str | None:
    """Extended quality checks for consistent strategy code."""

    # Check STRATEGY_PARAMS dict at module level (handles both x = ... and x: T = ...)
    has_strategy_params = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "STRATEGY_PARAMS"
            for t in node.targets
        ):
            has_strategy_params = True
            break
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "STRATEGY_PARAMS"
        ):
            has_strategy_params = True
            break
    if not has_strategy_params:
        return (
            "Missing module-level STRATEGY_PARAMS dict. "
            "Define STRATEGY_PARAMS: dict[str, Any] = {...} before the class."
        )

    # Check __init__ accepts **kwargs and merges with STRATEGY_PARAMS
    init_error = _verify_init_pattern(code, strategy_cls)
    if init_error:
        return init_error

    # Check indicator value validation (isfinite for scalar, isinstance for dict)
    has_isfinite = "math.isfinite" in code or "isfinite" in code
    has_isinstance_dict = "isinstance(" in code and "dict)" in code
    if not has_isfinite and not has_isinstance_dict:
        return (
            "Missing indicator value validation. "
            "Use math.isfinite() to guard against NaN/Inf indicator values."
        )

    # Check position_size usage for entry/exit safety
    if "position_size" not in code:
        return (
            "Missing position_size check. "
            "Check ctx.position_size before entry/exit to prevent duplicate positions."
        )

    return None


def _verify_init_pattern(code: str, strategy_cls: ast.ClassDef) -> str | None:
    """Verify __init__ follows the **kwargs + STRATEGY_PARAMS merge pattern."""
    init_method: ast.FunctionDef | None = None
    for node in ast.walk(strategy_cls):
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            init_method = node
            break

    if init_method is None:
        return f"Class '{strategy_cls.name}' is missing __init__ method."

    if not init_method.args.kwarg:
        return (
            f"Class '{strategy_cls.name}' __init__ must accept **kwargs "
            "and merge with STRATEGY_PARAMS (p = {{**STRATEGY_PARAMS, **kwargs}})."
        )

    init_src = ast.get_source_segment(code, init_method) or ""
    if "STRATEGY_PARAMS" not in init_src:
        return (
            f"Class '{strategy_cls.name}' __init__ must merge kwargs with "
            "STRATEGY_PARAMS (p = {{**STRATEGY_PARAMS, **kwargs}})."
        )

    return None


def _user_prompt_text(prompt: str, messages: list[ChatMessage]) -> str:
    if prompt:
        return prompt
    if messages:
        return " ".join(str(m.content or "") for m in messages if m.role == "user")
    return ""


@app.post("/test", response_model=TestResponse)
async def test_llm(
    body: TestRequest,
    _: None = Depends(require_api_key),
) -> TestResponse:
    config = get_config()
    if not config.is_azure_configured():
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI not configured (missing env vars)",
        )
    try:
        content, _ = chat_completion(
            config,
            system_content=TEST_SYSTEM_PROMPT,
            user_content=(body.input or "").strip() or "Hello",
        )
    except Exception as e:
        _raise_llm_http_error("/test", e)
    return TestResponse(output=(content or "").strip())


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
        system_content = build_strategy_chat_system_prompt(code, body.summary)
        openai_messages = [{"role": m.role, "content": m.content} for m in messages]
        content, _ = chat_completion_messages(
            config,
            system_content=system_content,
            messages=openai_messages,
        )
    except Exception as e:
        _raise_llm_http_error("/strategy/chat", e)

    if not content or not content.strip():
        logger.warning("LLM returned blank content at /strategy/chat")
        raise HTTPException(status_code=502, detail="Empty completion from model")

    if _is_model_refusal(content):
        logger.info("Model self-refusal detected in /strategy/chat")
        return StrategyChatResponse(content=_NON_TRADING_REJECTION_MSG)

    return StrategyChatResponse(content=content.strip())


async def _strategy_chat_stream_body(body: StrategyChatRequest):
    code = (body.code or "").strip()
    messages = body.messages or []
    if not messages:
        yield f"data: {json.dumps({'error': 'messages must be non-empty'})}\n\n"
        return

    config = get_config()
    if not config.is_azure_configured():
        yield f"data: {json.dumps({'error': 'Azure OpenAI not configured'})}\n\n"
        return

    try:
        system_content = build_strategy_chat_system_prompt(code, body.summary)
        openai_messages = [{"role": m.role, "content": m.content} for m in messages]
        acc: list[str] = []
        async for token in chat_completion_stream(
            config,
            system_content=system_content,
            messages=openai_messages,
        ):
            acc.append(token)
            yield f"data: {json.dumps({'token': token})}\n\n"
        full_text = "".join(acc)
        if _is_model_refusal(full_text):
            logger.info("Model self-refusal detected in /strategy/chat/stream")
            yield f"data: {json.dumps({'refusal_replace': _NON_TRADING_REJECTION_MSG})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"
    except Exception as e:
        logger.exception("LLM stream failed at /strategy/chat/stream: %s", e)
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


@app.post("/strategy/chat/stream")
async def strategy_chat_stream(
    body: StrategyChatRequest,
    _: None = Depends(require_api_key),
):
    return StreamingResponse(
        _strategy_chat_stream_body(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        _raise_llm_http_error("/intake", e)

    if not content or not content.strip():
        logger.warning("LLM returned blank content at /intake")
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
            model=config.resolved_reviewer_model,
        )
    except Exception as e:
        _raise_llm_http_error("/repair", e)

    if not content or not content.strip():
        logger.warning("LLM returned blank content at /repair")
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
        _raise_llm_http_error("/summarize", e)

    if not content or not content.strip():
        logger.warning("LLM returned blank content at /summarize")
        raise HTTPException(status_code=502, detail="Empty completion from model")

    return SummarizeResponse(summary=content.strip())


@app.post("/strategy/analyze", response_model=AnalyzeResponse)
async def analyze_strategy(
    body: AnalyzeRequest,
    _: None = Depends(require_api_key),
) -> AnalyzeResponse:
    """Analyze backtest results with a lightweight analyst model."""
    code = (body.code or "").strip()
    backtest_results = (body.backtest_results or "").strip()
    if not code or not backtest_results:
        raise HTTPException(status_code=422, detail="code and backtest_results must be non-empty")

    config = get_config()
    if not config.is_azure_configured():
        raise HTTPException(status_code=503, detail="Azure OpenAI not configured")

    user_content = (
        f"Strategy code:\n```python\n{code}\n```\n\n"
        f"Backtest results:\n{backtest_results}"
    )
    if body.summary:
        user_content += f"\n\nStrategy summary:\n{body.summary}"

    try:
        content, _ = chat_completion(
            config,
            system_content=build_analyst_system_prompt(),
            user_content=user_content,
            model=config.resolved_analyst_model,
            text_format={"type": "json_object"},
        )
    except Exception as e:
        _raise_llm_http_error("/strategy/analyze", e)

    if not content or not content.strip():
        raise HTTPException(status_code=502, detail="Empty completion from model")

    try:
        analysis = json.loads(content.strip())
    except json.JSONDecodeError:
        analysis = {"raw_analysis": content.strip()}

    return AnalyzeResponse(analysis=analysis)


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

    prompt_text = _user_prompt_text(prompt, messages)

    try:
        system_content = build_system_prompt(user_prompt=prompt_text)
        coder_model = config.resolved_coder_model
        if messages:
            openai_messages = [{"role": m.role, "content": m.content} for m in messages]
            content, model_used = chat_completion_messages(
                config,
                system_content=system_content,
                messages=openai_messages,
                model=coder_model,
            )
        else:
            content, model_used = chat_completion(
                config,
                system_content=system_content,
                user_content=prompt,
                model=coder_model,
            )
    except Exception as e:
        _raise_llm_http_error("/generate", e)

    if not content or not content.strip():
        logger.warning("LLM returned blank content at /generate")
        raise HTTPException(status_code=502, detail="Empty completion from model")

    code = _sanitize_code_quotes(_extract_python_code(content))

    reviewer_model = config.resolved_reviewer_model
    for attempt in range(_MAX_REPAIR_ATTEMPTS):
        error = _verify_strategy_code(code)
        if not error:
            break
        logger.info(
            "Strategy verification failed (attempt %d/%d): %s",
            attempt + 1,
            _MAX_REPAIR_ATTEMPTS,
            error,
        )
        repair_parts = [
            "Fix the strategy code based on the verification failure below.",
            "",
            f"Verification error:\n{error}",
        ]
        if prompt_text:
            repair_parts.extend(["", f"Original user request:\n{prompt_text}"])
        repair_parts.extend(["", "Current code:", code])
        try:
            repaired, model_used = chat_completion(
                config,
                system_content=build_repair_system_prompt(),
                user_content="\n".join(repair_parts),
                model=reviewer_model,
            )
            if repaired and repaired.strip():
                candidate = _sanitize_code_quotes(_extract_python_code(repaired))
                if candidate and ("class " in candidate or "def " in candidate):
                    code = candidate
                else:
                    logger.warning("Repair attempt %d returned non-code output", attempt + 1)
                    break
        except Exception:
            logger.exception("Repair attempt %d failed", attempt + 1)
            break

    return StrategyResponse(code=code, model_used=model_used)


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

    prompt_text = _user_prompt_text(prompt, messages)

    # Phase 1: Planning — Planner agent analyzes requirements and produces a structured spec
    yield f"data: {json.dumps({'phase': 'planning'})}\n\n"

    plan_spec: dict[str, Any] | None = None
    try:
        planner_input = prompt_text
        if messages:
            planner_parts = [f"[{m.role}]: {m.content}" for m in messages if m.content]
            planner_input = "\n".join(planner_parts)
        # Responses API requires the word "json" in user input for json_object format
        planner_input = f"Analyze the following trading strategy request and respond in JSON format.\n\n{planner_input}"

        plan_content, _ = chat_completion(
            config,
            system_content=build_planner_system_prompt(),
            user_content=planner_input,
            model=config.resolved_planner_model,
            text_format={"type": "json_object"},
        )
        plan_spec = _extract_json_object(plan_content)
        if plan_spec:
            logger.info("Planner produced spec: strategy_name=%s", plan_spec.get("strategy_name", "?"))
    except Exception:
        logger.warning("Planner agent failed, proceeding with direct generation", exc_info=True)

    # Gatekeeping: if planner says request is NOT trading-related, reject early
    if plan_spec and plan_spec.get("is_trading_related") is False:
        logger.info("Non-trading request rejected by planner")
        yield f"data: {json.dumps({'done': True, 'rejected': True, 'code': _NON_TRADING_REJECTION_MSG, 'repaired': False, 'repair_attempts': 0})}\n\n"
        return

    # Intent routing: if planner classifies as "question", signal frontend to use chat instead
    if plan_spec and plan_spec.get("intent") == "question":
        logger.info("Planner classified request as question — routing to chat")
        yield f"data: {json.dumps({'intent': 'question'})}\n\n"
        return

    # DSL fast path: if plan_spec is DSL-compatible, generate code deterministically
    dsl_code: str | None = None
    if plan_spec and plan_spec.get("intent") == "modify":
        try:
            from relay.strategy_dsl import generate_strategy_code, parse_planner_dsl

            dsl = parse_planner_dsl(plan_spec)
            if dsl and not dsl.needs_llm_fallback():
                logger.info("DSL fast path: generating %s deterministically", dsl.strategy_name)
                dsl_code = generate_strategy_code(dsl)
                verification_error = _verify_strategy_code(dsl_code)
                if verification_error:
                    logger.warning("DSL-generated code failed verification: %s — falling back to LLM", verification_error)
                    dsl_code = None
        except Exception:
            logger.warning("DSL code generation failed, falling back to LLM", exc_info=True)
            dsl_code = None

    if dsl_code:
        # Stream the DSL-generated code to the frontend
        yield f"data: {json.dumps({'phase': 'generating', 'progress': 0})}\n\n"
        # Emit tokens in chunks to maintain streaming UX
        chunk_size = 80
        for i in range(0, len(dsl_code), chunk_size):
            chunk = dsl_code[i : i + chunk_size]
            yield f"data: {json.dumps({'token': chunk})}\n\n"
        yield f"data: {json.dumps({'phase': 'verifying'})}\n\n"
        yield f"data: {json.dumps({'done': True, 'code': dsl_code, 'repaired': False, 'repair_attempts': 0})}\n\n"
        return

    # Phase 2: Generating — Coder agent writes the code (streaming)
    yield f"data: {json.dumps({'phase': 'generating', 'progress': 0})}\n\n"

    try:
        system_content = build_system_prompt(user_prompt=prompt_text)
        if plan_spec:
            plan_json = json.dumps(plan_spec, indent=2, ensure_ascii=False)
            system_content += f"\n\n## Implementation Plan\n\nFollow this specification produced by the planning agent:\n\n```json\n{plan_json}\n```"

        coder_model = config.resolved_coder_model
        code_acc: list[str] = []
        token_count = 0
        if messages:
            openai_messages = [{"role": m.role, "content": m.content} for m in messages]
            async for token in chat_completion_stream(
                config,
                system_content=system_content,
                messages=openai_messages,
                model=coder_model,
                enable_continuation=True,
            ):
                code_acc.append(token)
                token_count += 1
                yield f"data: {json.dumps({'token': token})}\n\n"
                if token_count % 50 == 0:
                    yield f"data: {json.dumps({'phase': 'generating', 'progress': min(90, token_count // 8)})}\n\n"
        else:
            async for token in chat_completion_stream(
                config,
                system_content=system_content,
                user_content=prompt,
                model=coder_model,
                enable_continuation=True,
            ):
                code_acc.append(token)
                token_count += 1
                yield f"data: {json.dumps({'token': token})}\n\n"
                if token_count % 50 == 0:
                    yield f"data: {json.dumps({'phase': 'generating', 'progress': min(90, token_count // 8)})}\n\n"

        raw_code = "".join(code_acc)

        # Detect model-level safety refusal (not a content filter, but built-in model safety)
        if _is_model_refusal(raw_code):
            logger.info("Model self-refusal detected in code generation stream")
            yield f"data: {json.dumps({'done': True, 'rejected': True, 'code': _NON_TRADING_REJECTION_MSG, 'repaired': False, 'repair_attempts': 0})}\n\n"
            return

        code = _sanitize_code_quotes(_extract_python_code(raw_code))
        if not code:
            yield f"data: {json.dumps({'error': 'Empty code from stream'})}\n\n"
            return

        # Phase 3: Verifying — Reviewer agent checks the code
        yield f"data: {json.dumps({'phase': 'verifying'})}\n\n"
        verification_error = _verify_strategy_code(code)

        repaired = False
        repair_attempts = 0
        reviewer_model = config.resolved_reviewer_model
        for attempt in range(_MAX_REPAIR_ATTEMPTS):
            if verification_error is None:
                break
            # Phase 4: Repairing — Reviewer agent fixes the code
            repair_attempts = attempt + 1
            yield f"data: {json.dumps({'phase': 'repairing', 'attempt': repair_attempts, 'max_attempts': _MAX_REPAIR_ATTEMPTS})}\n\n"

            logger.info(
                "Stream strategy verification failed (attempt %d/%d): %s",
                repair_attempts,
                _MAX_REPAIR_ATTEMPTS,
                verification_error,
            )
            repair_parts = [
                "Fix the strategy code based on the verification failure below.",
                "",
                f"Verification error:\n{verification_error}",
            ]
            if prompt_text:
                repair_parts.extend(["", f"Original user request:\n{prompt_text}"])
            repair_parts.extend(["", "Current code:", code])
            try:
                repaired_content, _ = chat_completion(
                    config,
                    system_content=build_repair_system_prompt(),
                    user_content="\n".join(repair_parts),
                    model=reviewer_model,
                )
                if repaired_content and repaired_content.strip():
                    candidate = _sanitize_code_quotes(_extract_python_code(repaired_content))
                    # Only accept repair if it looks like Python code (has class/def)
                    if candidate and ("class " in candidate or "def " in candidate):
                        code = candidate
                        repaired = True
                    else:
                        logger.warning("Repair attempt %d returned non-code output, keeping original", repair_attempts)
                        break
            except Exception:
                logger.exception("Stream repair attempt %d failed", repair_attempts)
                break
            verification_error = _verify_strategy_code(code)

        yield f"data: {json.dumps({'done': True, 'code': code, 'repaired': repaired, 'repair_attempts': repair_attempts})}\n\n"
    except Exception as e:
        logger.exception("LLM stream failed at /generate/stream: %s", e)
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
