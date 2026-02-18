"""Azure OpenAI/Foundry OpenAI client using Entra ID credentials.

Auth strategy:
1) Use ClientSecretCredential when tenant/client/secret env vars are provided.
2) Otherwise use DefaultAzureCredential (Managed Identity preferred in Azure runtime).
"""

from __future__ import annotations

import inspect
import json
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator

from azure.ai.projects import AIProjectClient
from azure.ai.projects.aio import AIProjectClient as AsyncAIProjectClient
from azure.core.credentials import TokenCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity import ClientSecretCredential, DefaultAzureCredential, get_bearer_token_provider
from azure.identity.aio import ClientSecretCredential as AsyncClientSecretCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from openai import AsyncAzureOpenAI, AzureOpenAI

from relay.config import RelayConfig

logger = logging.getLogger(__name__)


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


def _foundry_openai_kwargs(config: RelayConfig) -> dict[str, str]:
    kwargs: dict[str, str] = {}
    if config.azure_ai_project_connection_name:
        kwargs["connection_name"] = config.azure_ai_project_connection_name
    api_version = (config.azure_ai_project_openai_api_version or config.azure_openai_api_version).strip()
    if not api_version:
        raise ValueError(
            "Missing API version: set AZURE_AI_PROJECT_OPENAI_API_VERSION or AZURE_OPENAI_API_VERSION."
        )
    kwargs["api_version"] = api_version
    return kwargs


@contextmanager
def _foundry_sync_client(config: RelayConfig):
    credential = _build_credential(config)
    with AIProjectClient(
        endpoint=config.azure_ai_project_endpoint.rstrip("/"),
        credential=credential,
    ) as project_client:
        client = project_client.get_openai_client(**_foundry_openai_kwargs(config))
        try:
            yield client
        finally:
            closer = getattr(client, "close", None)
            if callable(closer):
                closer()


@asynccontextmanager
async def _foundry_async_client(config: RelayConfig):
    credential = _build_async_credential(config)
    try:
        async with AsyncAIProjectClient(
            endpoint=config.azure_ai_project_endpoint.rstrip("/"),
            credential=credential,
        ) as project_client:
            client_or_awaitable = project_client.get_openai_client(**_foundry_openai_kwargs(config))
            client = (
                await client_or_awaitable
                if inspect.isawaitable(client_or_awaitable)
                else client_or_awaitable
            )
            try:
                yield client
            finally:
                closer = getattr(client, "close", None)
                if callable(closer):
                    maybe_awaitable = closer()
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
    finally:
        await credential.close()


def _create_client(config: RelayConfig) -> AzureOpenAI:
    api_version = config.azure_openai_api_version.strip()
    if not api_version:
        raise ValueError("Missing API version: set AZURE_OPENAI_API_VERSION.")
    credential = _build_credential(config)
    token_provider = get_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureOpenAI(
        azure_endpoint=config.azure_openai_endpoint.rstrip("/"),
        azure_ad_token_provider=token_provider,
        api_version=api_version,
    )


def _create_async_client(config: RelayConfig) -> AsyncAzureOpenAI:
    api_version = config.azure_openai_api_version.strip()
    if not api_version:
        raise ValueError("Missing API version: set AZURE_OPENAI_API_VERSION.")
    credential = _build_credential(config)
    token_provider = get_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default",
    )
    return AsyncAzureOpenAI(
        azure_endpoint=config.azure_openai_endpoint.rstrip("/"),
        azure_ad_token_provider=token_provider,
        api_version=api_version,
    )


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


def _build_empty_response_detail(response: object) -> dict[str, object]:
    detail: dict[str, object] = {}
    model_used = getattr(response, "model", None)
    if model_used:
        detail["model"] = str(model_used)
    choices = getattr(response, "choices", None)
    if not choices:
        detail["reason"] = "no_choices"
        prompt_filters = _serialize_diagnostic(getattr(response, "prompt_filter_results", None))
        if prompt_filters:
            detail["prompt_filter_results"] = prompt_filters
        return detail

    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason is not None:
        detail["finish_reason"] = str(finish_reason)

    prompt_filters = _serialize_diagnostic(getattr(response, "prompt_filter_results", None))
    if prompt_filters:
        detail["prompt_filter_results"] = prompt_filters

    choice_filters = _serialize_diagnostic(getattr(choice, "content_filter_results", None))
    if choice_filters:
        detail["content_filter_results"] = choice_filters

    message = getattr(choice, "message", None)
    refusal = getattr(message, "refusal", None) if message is not None else None
    if refusal:
        detail["refusal"] = str(refusal)

    content = getattr(message, "content", None) if message is not None else None
    if content is None:
        detail["reason"] = detail.get("reason", "missing_content")
    elif isinstance(content, str) and not content.strip():
        detail["reason"] = detail.get("reason", "empty_content")
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
    """Stream OpenAI Chat Completions; yield content deltas."""
    if messages:
        full_messages = [{"role": "system", "content": system_content}] + messages
    else:
        full_messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content or ""},
        ]

    emitted = False
    last_finish_reason: str | None = None
    if config.is_foundry_project_mode():
        async with _foundry_async_client(config) as client:
            stream = await client.chat.completions.create(
                model=config.azure_openai_model,
                messages=full_messages,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices:
                    finish_reason = getattr(chunk.choices[0], "finish_reason", None)
                    if finish_reason is not None:
                        last_finish_reason = str(finish_reason)
                    if chunk.choices[0].delta.content is not None:
                        emitted = True
                        yield chunk.choices[0].delta.content
        if not emitted:
            diag = {"reason": "empty_stream", "finish_reason": last_finish_reason}
            logger.warning("LLM returned empty stream: %s", json.dumps(diag, ensure_ascii=False))
            raise ValueError(f"Empty streamed completion from model. diagnostics={json.dumps(diag)}")
        return

    client = _create_async_client(config)
    stream = await client.chat.completions.create(
        model=config.azure_openai_model,
        messages=full_messages,
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices:
            finish_reason = getattr(chunk.choices[0], "finish_reason", None)
            if finish_reason is not None:
                last_finish_reason = str(finish_reason)
            if chunk.choices[0].delta.content is not None:
                emitted = True
                yield chunk.choices[0].delta.content
    if not emitted:
        diag = {"reason": "empty_stream", "finish_reason": last_finish_reason}
        logger.warning("LLM returned empty stream: %s", json.dumps(diag, ensure_ascii=False))
        raise ValueError(f"Empty streamed completion from model. diagnostics={json.dumps(diag)}")


def chat_completion(
    config: RelayConfig,
    system_content: str,
    user_content: str,
) -> tuple[str, str]:
    """Call Chat Completions. Returns (content, model_used)."""
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    if config.is_foundry_project_mode():
        with _foundry_sync_client(config) as client:
            response = client.chat.completions.create(
                model=config.azure_openai_model,
                messages=messages,
            )
    else:
        client = _create_client(config)
        response = client.chat.completions.create(
            model=config.azure_openai_model,
            messages=messages,
        )

    choice = response.choices[0] if response.choices else None
    if not choice or not choice.message or choice.message.content is None:
        _raise_empty_completion(response)
    content = choice.message.content
    if isinstance(content, str) and not content.strip():
        _raise_empty_completion(response)
    model_used = getattr(response, "model", None) or config.azure_openai_model
    return content, model_used


def chat_completion_messages(
    config: RelayConfig,
    system_content: str,
    messages: list[dict[str, str]],
) -> tuple[str, str]:
    """Call Chat Completions with multi-turn messages. Returns (content, model_used)."""
    full_messages = [{"role": "system", "content": system_content}] + messages

    if config.is_foundry_project_mode():
        with _foundry_sync_client(config) as client:
            response = client.chat.completions.create(
                model=config.azure_openai_model,
                messages=full_messages,
            )
    else:
        client = _create_client(config)
        response = client.chat.completions.create(
            model=config.azure_openai_model,
            messages=full_messages,
        )

    choice = response.choices[0] if response.choices else None
    if not choice or not choice.message or choice.message.content is None:
        _raise_empty_completion(response)
    content = choice.message.content
    if isinstance(content, str) and not content.strip():
        _raise_empty_completion(response)
    model_used = getattr(response, "model", None) or config.azure_openai_model
    return content, model_used
