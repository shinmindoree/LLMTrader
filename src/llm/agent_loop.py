"""Agent-based strategy generation engine.

Implements a Copilot-like agentic loop where the LLM autonomously:
1. Reads SKILL.md and interface files
2. Explores existing strategies for reference
3. Generates strategy code
4. Tests it (load + backtest)
5. Fixes errors and retries

Uses OpenAI Responses API with function-calling tools.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from llm.agent_tools import AGENT_TOOLS, execute_tool
from llm.config import RelayConfig

logger = logging.getLogger(__name__)

# Max agent iterations (tool calls + responses). Copilot typically does 10-15.
_MAX_ITERATIONS = 20

# Tools that are "terminal" — agent signals completion
_TERMINAL_TOOLS = frozenset({"done"})

# Tools that produce large outputs — stream a progress indicator
_SLOW_TOOLS = frozenset({"write_strategy", "run_backtest"})


async def agent_generate_stream(
    config: RelayConfig,
    system_prompt: str,
    user_prompt: str,
    messages: list[dict[str, str]] | None = None,
    *,
    model: str | None = None,
    confirmed_plan: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the agent loop, yielding SSE-compatible event dicts.

    Events emitted:
    - {"phase": "agent", "step": N, "tool": "tool_name"}  — tool call happening
    - {"phase": "agent_thinking"}  — LLM is thinking
    - {"token": "..."}  — final code streaming tokens
    - {"plan_preview": "...", "plan_spec": {...}}  — plan preview (if planner enabled)
    - {"done": True, "code": "...", ...}  — generation complete
    - {"intent": "question"|"analyze"}  — non-code intent routed to chat
    - {"error": "..."}  — error
    """
    from openai import AsyncOpenAI
    from azure.identity.aio import (
        ClientSecretCredential as AsyncClientSecretCredential,
        DefaultAzureCredential as AsyncDefaultAzureCredential,
    )
    from azure.identity.aio import get_bearer_token_provider as get_async_bearer_token_provider
    import httpx

    resolved_model = model or config.resolved_coder_model

    # Build credential
    if config.has_client_secret_credential():
        credential = AsyncClientSecretCredential(
            tenant_id=config.azure_tenant_id,
            client_id=config.azure_client_id,
            client_secret=config.azure_client_secret,
        )
    else:
        kwargs: dict[str, str] = {}
        if config.azure_client_id:
            kwargs["managed_identity_client_id"] = config.azure_client_id
        credential = AsyncDefaultAzureCredential(**kwargs)

    token_provider = get_async_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default",
    )

    client = AsyncOpenAI(
        base_url=config.resolved_openai_base_url.rstrip("/") + "/",
        api_key=token_provider,
        timeout=httpx.Timeout(300.0, connect=30.0),
    )

    try:
        # Build initial input
        input_items: list[dict[str, Any]] = []

        # If there's a confirmed plan, prepend it as context
        if confirmed_plan:
            plan_json = json.dumps(confirmed_plan, indent=2, ensure_ascii=False)
            plan_context = (
                f"The following implementation plan has been approved by the user. "
                f"Use it as a guide for the strategy structure:\n\n```json\n{plan_json}\n```\n\n"
            )
            if messages:
                # Prepend plan to the first user message
                enriched_messages = list(messages)
                enriched_messages[-1] = {
                    **enriched_messages[-1],
                    "content": plan_context + enriched_messages[-1].get("content", ""),
                }
                input_items = [
                    {"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in enriched_messages
                ]
            else:
                input_items = [{"role": "user", "content": plan_context + user_prompt}]
        elif messages:
            input_items = [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages
            ]
        else:
            input_items = [{"role": "user", "content": user_prompt}]

        # Tools — include web search if enabled
        tools: list[dict[str, Any]] = list(AGENT_TOOLS)
        if config.enable_web_search:
            tools.append({"type": "web_search_preview"})

        yield {"phase": "agent_thinking"}

        # Agent loop
        final_code: str | None = None
        final_filename: str | None = None
        final_summary: str | None = None
        iteration = 0
        response_id: str | None = None

        while iteration < _MAX_ITERATIONS:
            iteration += 1

            # Call the model
            request_kwargs: dict[str, Any] = {
                "model": resolved_model,
                "instructions": system_prompt,
                "tools": tools,
                "max_output_tokens": 16384,
            }

            if response_id:
                # Continue from previous response
                request_kwargs["previous_response_id"] = response_id
            else:
                request_kwargs["input"] = input_items

            try:
                response = await client.responses.create(**request_kwargs)
            except Exception as exc:
                logger.exception("Agent LLM call failed at iteration %d: %s", iteration, exc)
                yield {"error": f"LLM call failed: {exc}"}
                return

            response_id = getattr(response, "id", None)
            status = getattr(response, "status", "completed")

            # Process output items
            output = getattr(response, "output", []) or []
            has_tool_calls = False
            text_content = ""
            tool_outputs: list[dict[str, Any]] = []  # Collect ALL tool results

            for item in output:
                item_type = getattr(item, "type", None)

                if item_type == "function_call":
                    has_tool_calls = True
                    func_name = getattr(item, "name", "")
                    call_id = getattr(item, "call_id", "")
                    arguments_raw = getattr(item, "arguments", "{}")

                    try:
                        arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
                    except json.JSONDecodeError:
                        arguments = {}

                    logger.info("Agent tool call [%d]: %s(%s)", iteration, func_name, json.dumps(arguments, ensure_ascii=False)[:200])

                    # Handle terminal tool
                    if func_name in _TERMINAL_TOOLS:
                        final_code = arguments.get("code", "")
                        final_filename = arguments.get("filename", "")
                        final_summary = arguments.get("summary")
                        yield {
                            "done": True,
                            "code": final_code,
                            "filename": final_filename,
                            "summary": final_summary,
                            "repaired": False,
                            "repair_attempts": 0,
                            "agent_iterations": iteration,
                        }
                        return

                    # Emit progress event
                    yield {
                        "phase": "agent",
                        "step": iteration,
                        "tool": func_name,
                        "tool_input": _summarize_tool_input(func_name, arguments),
                    }

                    # Execute tool
                    tool_result = execute_tool(func_name, arguments)

                    # Stream code tokens if write_strategy succeeded
                    if func_name == "write_strategy" and tool_result.startswith("OK:"):
                        code = arguments.get("code", "")
                        if code:
                            # Stream the code to frontend
                            chunk_size = 80
                            for i in range(0, len(code), chunk_size):
                                yield {"token": code[i:i + chunk_size]}

                    # Collect tool result for feeding back
                    tool_outputs.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": tool_result[:16000],  # Allow generous result size
                    })

                elif item_type == "message":
                    content_parts = getattr(item, "content", []) or []
                    for part in content_parts:
                        if getattr(part, "type", None) == "output_text":
                            text = getattr(part, "text", "")
                            if text:
                                text_content += text

            # Feed ALL tool results back for next iteration
            if tool_outputs:
                input_items = tool_outputs
                response_id = getattr(response, "id", None)

            # If LLM produced text without tool calls, it might be:
            # - A plan/analysis response (route to chat)
            # - Final code in text form (extract and verify)
            if not has_tool_calls:
                if text_content.strip():
                    # Check if this looks like code
                    if "class " in text_content and "Strategy" in text_content:
                        # LLM produced code directly without using write_strategy
                        code = _extract_code_from_text(text_content)
                        if code:
                            yield {
                                "done": True,
                                "code": code,
                                "repaired": False,
                                "repair_attempts": 0,
                                "agent_iterations": iteration,
                            }
                            return
                    # Might be analysis/question response
                    yield {
                        "done": True,
                        "code": text_content,
                        "repaired": False,
                        "repair_attempts": 0,
                        "agent_iterations": iteration,
                    }
                    return

                # No output at all — likely an issue
                logger.warning("Agent iteration %d produced no output", iteration)
                yield {"error": "Agent produced no output."}
                return

        # Max iterations reached
        logger.warning("Agent reached max iterations (%d)", _MAX_ITERATIONS)
        yield {"error": f"Agent did not complete within {_MAX_ITERATIONS} iterations."}

    finally:
        await client.close()
        await credential.close()


def _summarize_tool_input(name: str, args: dict[str, Any]) -> str:
    """Create a short summary of tool input for the progress event."""
    if name == "read_file":
        return args.get("path", "")
    if name == "search_code":
        return args.get("query", "")
    if name == "write_strategy":
        return args.get("filename", "")
    if name == "run_backtest":
        return args.get("filename", "")
    if name == "list_strategies":
        return ""
    return json.dumps(args, ensure_ascii=False)[:100]


def _extract_code_from_text(text: str) -> str | None:
    """Extract Python code from a text response that may contain markdown."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        # Raw code
        if "class " in stripped and "Strategy" in stripped:
            return stripped
        return None

    # Markdown code block
    lines = stripped.split("\n")
    start = 1
    if start < len(lines) and lines[start].strip().lower() in ("python", "py", "python3"):
        start += 1
    elif lines[0].strip().lower().startswith("```python"):
        pass  # start is already 1

    end = len(lines)
    for i in range(len(lines) - 1, 0, -1):
        if lines[i].strip() == "```":
            end = i
            break

    code = "\n".join(lines[start:end]).strip()
    if "class " in code and "Strategy" in code:
        return code
    return None
