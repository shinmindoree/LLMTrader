"""Strategy code generation logic (planning, coding, verification, repair).

Extracted from the former standalone relay server. Contains the core
streaming generation pipeline and supporting helpers used by LLMClient.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

from pydantic import BaseModel

from llm.azure_openai import chat_completion, chat_completion_stream
from llm.capability_registry import (
    build_development_requirements,
    capability_summary_lines,
    detect_unsupported_requirements,
)
from llm.config import get_config
from llm.prompts import (
    build_planner_system_prompt,
    build_repair_system_prompt,
    build_system_prompt,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models shared with LLMClient
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str


class StrategyRequest(BaseModel):
    user_prompt: str
    messages: list[ChatMessage] | None = None
    confirmed_plan: dict[str, Any] | None = None  # Pre-confirmed plan spec to skip planner


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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

_MAX_REPAIR_ATTEMPTS = 3

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
        "symbol", "ticker", "market", "pair",
        "심볼", "종목", "티커", "거래쌍", "자산",
    ),
    "timeframe": (
        "timeframe", "interval", "candle", "timescale",
        "타임프레임", "캔들", "캔들간격", "봉", "시간간격",
    ),
    "entry": (
        "entry", "enter", "buycondition",
        "진입", "매수조건", "롱조건", "숏조건",
    ),
    "risk": (
        "risk", "position", "size", "leverage", "drawdown",
        "리스크", "위험관리", "수량", "비중", "레버리지", "손실한도",
    ),
    "exit": (
        "exit", "close", "takeprofit", "stoploss",
        "청산", "익절", "손절", "종료",
    ),
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _is_model_refusal(text: str) -> bool:
    """Detect model-level safety refusal messages."""
    stripped = text.strip()
    if not stripped:
        return False
    return any(p.search(stripped) for p in _MODEL_REFUSAL_PATTERNS)


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


def _build_plan_preview_text(plan_spec: dict[str, Any]) -> str:
    """Build a human-readable Korean summary from a planner spec."""
    lines: list[str] = []
    name = plan_spec.get("strategy_name", "")
    desc = plan_spec.get("description", "")
    if name:
        lines.append(f"**전략명**: {name}")
    if desc:
        lines.append(f"**설명**: {desc}")
    direction = plan_spec.get("direction", "")
    if direction:
        direction_map = {"long_only": "롱 전용", "short_only": "숏 전용", "long_short": "양방향"}
        lines.append(f"**방향**: {direction_map.get(direction, direction)}")
    symbol = plan_spec.get("symbol", "")
    tf = plan_spec.get("timeframe", "")
    if symbol or tf:
        lines.append(f"**심볼/타임프레임**: {symbol or '?'} / {tf or '?'}")
    indicators = plan_spec.get("indicators", [])
    if indicators:
        ind_strs = []
        for ind in indicators:
            ind_name = ind.get("name", "?")
            params = ind.get("params", {})
            param_str = ", ".join(f"{k}={v}" for k, v in params.items()) if params else ""
            ind_strs.append(f"{ind_name}({param_str})" if param_str else ind_name)
        lines.append(f"**지표**: {', '.join(ind_strs)}")
    entry_long = plan_spec.get("entry_long")
    if entry_long and isinstance(entry_long, dict):
        lines.append(f"**롱 진입**: {entry_long.get('reason_template', entry_long.get('condition_expr', ''))}")
    exit_long = plan_spec.get("exit_long")
    if exit_long and isinstance(exit_long, dict):
        lines.append(f"**롱 청산**: {exit_long.get('reason_template', exit_long.get('condition_expr', ''))}")
    entry_short = plan_spec.get("entry_short")
    if entry_short and isinstance(entry_short, dict):
        lines.append(f"**숏 진입**: {entry_short.get('reason_template', entry_short.get('condition_expr', ''))}")
    exit_short = plan_spec.get("exit_short")
    if exit_short and isinstance(exit_short, dict):
        lines.append(f"**숏 청산**: {exit_short.get('reason_template', exit_short.get('condition_expr', ''))}")
    risk = plan_spec.get("risk", {})
    if isinstance(risk, dict):
        sl = risk.get("stop_loss_pct")
        tp = risk.get("take_profit_pct")
        risk_parts = []
        if sl is not None:
            risk_parts.append(f"손절 {sl}%")
        if tp is not None:
            risk_parts.append(f"익절 {tp}%")
        if risk_parts:
            lines.append(f"**리스크**: {', '.join(risk_parts)}")
    custom = plan_spec.get("custom_indicator")
    if custom:
        lines.append(f"**커스텀 지표**: {custom}")
    return "\n".join(lines) if lines else "전략 스펙이 생성되었습니다."


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

    try:
        compile(code, "<strategy>", "exec")
    except Exception as e:
        return f"Compilation error: {e}"

    on_bar_error = _check_on_bar_variable_refs(tree)
    if on_bar_error:
        return on_bar_error

    return None


def _check_on_bar_variable_refs(tree: ast.Module) -> str | None:
    """Best-effort check that variables used in on_bar are defined."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not node.name.endswith("Strategy"):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "on_bar":
                return _verify_on_bar_names(item, tree)
    return None


def _verify_on_bar_names(on_bar: ast.FunctionDef, tree: ast.Module) -> str | None:
    """Check that variable names read in on_bar are plausibly defined."""
    assigned: set[str] = set()
    for node in ast.walk(on_bar):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            assigned.add(node.id)
        elif isinstance(node, ast.arg):
            assigned.add(node.arg)
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)

    module_names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            module_names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            module_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    module_names.add(t.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                module_names.add(alias.asname or alias.name.split(".")[-1])

    builtins_available = {
        "True", "False", "None", "int", "float", "str", "bool", "list", "dict",
        "set", "tuple", "len", "range", "abs", "min", "max", "sum", "round",
        "isinstance", "print", "type", "getattr", "hasattr", "setattr",
        "ValueError", "TypeError", "KeyError", "IndexError", "Exception",
        "math", "Any",
    }
    all_known = assigned | module_names | builtins_available

    for node in ast.walk(on_bar):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id == "self":
                continue
            if node.id not in all_known:
                return f"name '{node.id}' is not defined"
    return None


def _user_prompt_text(prompt: str, messages: list[ChatMessage]) -> str:
    if prompt:
        return prompt
    if messages:
        return " ".join(str(m.content or "") for m in messages if m.role == "user")
    return ""


# ---------------------------------------------------------------------------
# Main streaming generation pipeline
# ---------------------------------------------------------------------------

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

    # Enrich prompt with URL content if URLs are present
    from llm.url_fetcher import fetch_urls_from_text

    url_results = await fetch_urls_from_text(prompt_text)
    if url_results:
        url_sections = []
        for url, content in url_results:
            url_sections.append(f"---\n[{url}] 페이지 내용:\n\n{content}\n---")
        prompt_text = (
            f"{prompt_text}\n\n"
            "아래는 위 URL에서 추출한 페이지 내용입니다:\n\n"
            + "\n\n".join(url_sections)
        )

    # Phase 1: Planning — Planner agent analyzes requirements and produces a structured spec
    yield f"data: {json.dumps({'phase': 'planning'})}\n\n"

    plan_spec: dict[str, Any] | None = body.confirmed_plan
    plan_confirmed = plan_spec is not None

    if not plan_confirmed:
        try:
            planner_input = prompt_text
            if messages:
                planner_parts = [f"[{m.role}]: {m.content}" for m in messages if m.content]
                planner_input = "\n".join(planner_parts)
            planner_input = f"Analyze the following trading strategy request and respond in JSON format.\n\n{planner_input}"

            import asyncio as _asyncio

            async def _planner_task():
                return chat_completion(
                    config,
                    system_content=build_planner_system_prompt(),
                    user_content=planner_input,
                    model=config.resolved_planner_model,
                    text_format={"type": "json_object"},
                    enable_web_search=config.enable_web_search,
                )

            planner_future = _asyncio.ensure_future(_planner_task())
            while not planner_future.done():
                await _asyncio.sleep(10)
                if not planner_future.done():
                    yield f"data: {json.dumps({'heartbeat': True})}\n\n"
            plan_content, _ = planner_future.result()
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

    # Determine intent — default to "question" when planner failed or didn't provide intent
    intent = plan_spec.get("intent") if plan_spec else None

    # Route non-modify intents (and planner failures) to chat
    if intent in ("question", "analyze") or intent is None:
        effective_intent = intent or "question"
        if intent is None:
            logger.info("Planner failed or returned no spec — routing to chat as fallback")
        else:
            logger.info("Planner classified request as %s — routing to chat", effective_intent)
        yield f"data: {json.dumps({'intent': effective_intent})}\n\n"
        return

    # Unknown intent safety net — route to chat rather than generating code
    if intent != "modify":
        logger.warning("Unknown planner intent %r — routing to chat", intent)
        yield f"data: {json.dumps({'intent': 'question'})}\n\n"
        return

    # Plan preview: if planner produced a "modify" plan and user hasn't confirmed yet,
    # emit a preview and stop — frontend will re-invoke with confirmed_plan to proceed.
    if not plan_confirmed:
        preview_text = _build_plan_preview_text(plan_spec)
        logger.info("Emitting plan preview for user confirmation: %s", plan_spec.get("strategy_name", "?"))
        yield f"data: {json.dumps({'plan_preview': preview_text, 'plan_spec': plan_spec})}\n\n"
        return

    # -----------------------------------------------------------------------
    # Agent-based generation (replaces DSL + Coder + repair pipeline)
    # -----------------------------------------------------------------------
    from llm.agent_loop import agent_generate_stream
    from llm.prompts import build_agent_system_prompt

    agent_system = build_agent_system_prompt()
    agent_messages = (
        [{"role": m.role, "content": m.content} for m in messages]
        if messages
        else None
    )

    try:
        async for event in agent_generate_stream(
            config,
            system_prompt=agent_system,
            user_prompt=prompt_text,
            messages=agent_messages,
            confirmed_plan=plan_spec,
        ):
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("done") or event.get("error"):
                return
    except Exception as e:
        logger.exception("Agent generation failed, falling back to legacy pipeline: %s", e)
        # Fall through to legacy pipeline below

    # -----------------------------------------------------------------------
    # Legacy fallback: DSL fast path + LLM Coder + repair loop
    # -----------------------------------------------------------------------

    # DSL fast path: if plan_spec is DSL-compatible, generate code deterministically
    dsl_code: str | None = None
    if plan_spec and plan_spec.get("intent") == "modify":
        try:
            from llm.strategy_dsl import generate_strategy_code, parse_planner_dsl

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
        from llm.strategy_postprocess import ensure_ohlcv_bindings

        dsl_code = ensure_ohlcv_bindings(dsl_code)

        yield f"data: {json.dumps({'phase': 'generating', 'progress': 0})}\n\n"
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

        if _is_model_refusal(raw_code):
            logger.info("Model self-refusal detected in code generation stream")
            yield f"data: {json.dumps({'done': True, 'rejected': True, 'code': _NON_TRADING_REJECTION_MSG, 'repaired': False, 'repair_attempts': 0})}\n\n"
            return

        code = _sanitize_code_quotes(_extract_python_code(raw_code))
        if not code:
            yield f"data: {json.dumps({'error': 'Empty code from stream'})}\n\n"
            return

        # Phase 3: Verifying
        yield f"data: {json.dumps({'phase': 'verifying'})}\n\n"
        verification_error = _verify_strategy_code(code)

        repaired = False
        repair_attempts = 0
        reviewer_model = config.resolved_reviewer_model
        for attempt in range(_MAX_REPAIR_ATTEMPTS):
            if verification_error is None:
                break
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
                import asyncio as _asyncio

                async def _repair_task():
                    return chat_completion(
                        config,
                        system_content=build_repair_system_prompt(),
                        user_content="\n".join(repair_parts),
                        model=reviewer_model,
                    )

                repair_future = _asyncio.ensure_future(_repair_task())
                while not repair_future.done():
                    await _asyncio.sleep(10)
                    if not repair_future.done():
                        yield f"data: {json.dumps({'heartbeat': True})}\n\n"
                repaired_content, _ = repair_future.result()
                if repaired_content and repaired_content.strip():
                    candidate = _sanitize_code_quotes(_extract_python_code(repaired_content))
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

        from llm.strategy_postprocess import ensure_ohlcv_bindings

        code = ensure_ohlcv_bindings(code)

        yield f"data: {json.dumps({'done': True, 'code': code, 'repaired': repaired, 'repair_attempts': repair_attempts})}\n\n"
    except Exception as e:
        logger.exception("LLM stream failed at generate/stream: %s", e)
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
