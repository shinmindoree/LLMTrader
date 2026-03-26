"""Azure OpenAI v1 client using Entra ID credentials.

Auth strategy:
1) Use ClientSecretCredential when tenant/client/secret env vars are provided.
2) Otherwise use DefaultAzureCredential (Managed Identity preferred in Azure runtime).
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from azure.core.credentials import TokenCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity import ClientSecretCredential, DefaultAzureCredential, get_bearer_token_provider
from azure.identity.aio import ClientSecretCredential as AsyncClientSecretCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from azure.identity.aio import get_bearer_token_provider as get_async_bearer_token_provider
from openai import AsyncOpenAI, OpenAI

from relay.config import RelayConfig

logger = logging.getLogger(__name__)

_ALLOWED_RESPONSE_ROLES = {"user", "assistant", "system", "developer"}


def _build_credential(config: RelayConfig) -> TokenCredential:
    if config.has_client_secret_credential():
        return ClientSecretCredential(
            tenant_id=config.azure_tenant_id,
            client_id=config.azure_client_id,
            client_secret=config.azure_client_secret,
        )

    kwargs: dict[str, str] = {}
    if config.azure_client_id:
        # Supports user-assigned managed identity when AZURE_CLIENT_ID is set.
        kwargs["managed_identity_client_id"] = config.azure_client_id
    return DefaultAzureCredential(**kwargs)


def _build_async_credential(config: RelayConfig) -> AsyncTokenCredential:
    if config.has_client_secret_credential():
        return AsyncClientSecretCredential(
            tenant_id=config.azure_tenant_id,
            client_id=config.azure_client_id,
            client_secret=config.azure_client_secret,
        )

    kwargs: dict[str, str] = {}
    if config.azure_client_id:
        kwargs["managed_identity_client_id"] = config.azure_client_id
    return AsyncDefaultAzureCredential(**kwargs)


# Timeout applied to every OpenAI API call (connection + read + write).
_OPENAI_TIMEOUT = httpx.Timeout(120.0, connect=30.0)
# Streaming calls may take longer before first token; use a generous read timeout.
_OPENAI_STREAM_TIMEOUT = httpx.Timeout(300.0, connect=30.0)

_STREAM_MAX_RETRIES = 2


def _create_client(config: RelayConfig) -> OpenAI:
    credential = _build_credential(config)
    token_provider = get_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default",
    )
    return OpenAI(
        base_url=config.resolved_openai_base_url.rstrip("/") + "/",
        api_key=token_provider,
        timeout=_OPENAI_TIMEOUT,
    )


@asynccontextmanager
async def _create_async_client(config: RelayConfig):
    credential = _build_async_credential(config)
    token_provider = get_async_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default",
    )
    client = AsyncOpenAI(
        base_url=config.resolved_openai_base_url.rstrip("/") + "/",
        api_key=token_provider,
        timeout=_OPENAI_TIMEOUT,
    )
    try:
        yield client
    finally:
        await client.close()
        await credential.close()


def _serialize_diagnostic(value: object) -> object:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(exclude_none=True)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)


def _get_attr(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _build_response_input(
    user_content: str | None = None,
    messages: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    if messages:
        items: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role") or "user").strip().lower() or "user"
            if role not in _ALLOWED_RESPONSE_ROLES:
                role = "user"
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            items.append({"role": role, "content": content})
        if items:
            return items
    return [{"role": "user", "content": (user_content or "").strip()}]


def _build_response_kwargs(
    config: RelayConfig,
    system_content: str,
    user_content: str | None = None,
    messages: list[dict[str, str]] | None = None,
    *,
    stream: bool = False,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "model": config.resolved_openai_model,
        "instructions": system_content,
        "input": _build_response_input(user_content=user_content, messages=messages),
    }
    if stream:
        kwargs["stream"] = True
    return kwargs


def _extract_response_output_text(response: object) -> str | None:
    output_text = _get_attr(response, "output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = _get_attr(response, "output")
    if not output:
        return None

    chunks: list[str] = []
    for item in output:
        if _get_attr(item, "type") != "message":
            continue
        content_parts = _get_attr(item, "content") or []
        for part in content_parts:
            part_type = _get_attr(part, "type")
            if part_type == "output_text":
                text = _get_attr(part, "text")
                if isinstance(text, str) and text:
                    chunks.append(text)
    joined = "".join(chunks).strip()
    return joined or None


def _build_empty_response_detail(response: object) -> dict[str, object]:
    detail: dict[str, object] = {}
    model_used = getattr(response, "model", None)
    if model_used:
        detail["model"] = str(model_used)

    status = _get_attr(response, "status")
    if status:
        detail["status"] = str(status)

    incomplete_details = _serialize_diagnostic(_get_attr(response, "incomplete_details"))
    if incomplete_details:
        detail["incomplete_details"] = incomplete_details

    error = _serialize_diagnostic(_get_attr(response, "error"))
    if error:
        detail["error"] = error

    output = _get_attr(response, "output") or []
    if not output:
        detail["reason"] = "no_output"
        return detail

    output_types: list[str] = []
    refusals: list[str] = []
    for item in output:
        item_type = _get_attr(item, "type")
        if item_type:
            output_types.append(str(item_type))
        if item_type != "message":
            continue
        content_parts = _get_attr(item, "content") or []
        for part in content_parts:
            if _get_attr(part, "type") != "refusal":
                continue
            refusal = _get_attr(part, "refusal")
            if isinstance(refusal, str) and refusal.strip():
                refusals.append(refusal.strip())

    if output_types:
        detail["output_types"] = output_types
    if refusals:
        detail["refusal"] = " ".join(refusals)
    detail["reason"] = detail.get("reason", "empty_output_text")
    return detail


def _raise_empty_completion(response: object) -> None:
    detail = _build_empty_response_detail(response)
    detail_text = json.dumps(detail, ensure_ascii=False, default=str)
    logger.warning("LLM returned empty completion: %s", detail_text)
    raise ValueError(f"Empty completion from model. diagnostics={detail_text}")


async def chat_completion_stream(
    config: RelayConfig,
    system_content: str,
    user_content: str | None = None,
    messages: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    """Stream OpenAI Responses API text deltas with pre-first-token retry."""
    request_kwargs = _build_response_kwargs(
        config,
        system_content=system_content,
        user_content=user_content,
        messages=messages,
        stream=True,
    )

    last_error: Exception | None = None
    for attempt in range(_STREAM_MAX_RETRIES + 1):
        emitted = False
        final_text: str | None = None
        try:
            async with _create_async_client(config) as client:
                stream = await client.responses.create(**request_kwargs)
                async for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "response.output_text.delta" and getattr(event, "delta", None):
                        emitted = True
                        yield event.delta
                    elif event_type == "response.output_text.done" and getattr(event, "text", None):
                        final_text = event.text
            if emitted:
                return
            if final_text and final_text.strip():
                yield final_text
                return
            last_error = ValueError("Empty streamed completion from model")
        except Exception as exc:
            last_error = exc
            if emitted:
                # Already yielded tokens — cannot safely retry.
                raise

        if attempt < _STREAM_MAX_RETRIES:
            wait = 1.0 * (attempt + 1)
            logger.warning(
                "Stream attempt %d/%d failed (%s), retrying in %.1fs…",
                attempt + 1,
                _STREAM_MAX_RETRIES + 1,
                last_error,
                wait,
            )
            await asyncio.sleep(wait)

    diag = {"reason": "empty_stream", "attempts": _STREAM_MAX_RETRIES + 1}
    logger.warning("LLM returned empty stream after retries: %s", json.dumps(diag, ensure_ascii=False))
    raise last_error or ValueError(f"Empty streamed completion from model. diagnostics={json.dumps(diag)}")


def chat_completion(
    config: RelayConfig,
    system_content: str,
    user_content: str,
) -> tuple[str, str]:
    """Call Responses API for a single user turn. Returns (content, model_used)."""
    request_kwargs = _build_response_kwargs(
        config,
        system_content=system_content,
        user_content=user_content,
    )

    client = _create_client(config)
    response = client.responses.create(**request_kwargs)

    content = _extract_response_output_text(response)
    if not content:
        _raise_empty_completion(response)
    model_used = getattr(response, "model", None) or config.resolved_openai_model
    return content, model_used


def chat_completion_messages(
    config: RelayConfig,
    system_content: str,
    messages: list[dict[str, str]],
) -> tuple[str, str]:
    """Call Responses API with multi-turn messages. Returns (content, model_used)."""
    request_kwargs = _build_response_kwargs(
        config,
        system_content=system_content,
        messages=messages,
    )

    client = _create_client(config)
    response = client.responses.create(**request_kwargs)

    content = _extract_response_output_text(response)
    if not content:
        _raise_empty_completion(response)
    model_used = getattr(response, "model", None) or config.resolved_openai_model
    return content, model_used
