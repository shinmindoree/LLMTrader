"""Azure OpenAI/Foundry OpenAI client using Entra ID credentials.

Auth strategy:
1) Use ClientSecretCredential when tenant/client/secret env vars are provided.
2) Otherwise use DefaultAzureCredential (Managed Identity preferred in Azure runtime).
"""

from __future__ import annotations

import inspect
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

    if config.is_foundry_project_mode():
        async with _foundry_async_client(config) as client:
            stream = await client.chat.completions.create(
                model=config.azure_openai_model,
                messages=full_messages,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content
        return

    client = _create_async_client(config)
    stream = await client.chat.completions.create(
        model=config.azure_openai_model,
        messages=full_messages,
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content is not None:
            yield chunk.choices[0].delta.content


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
        raise ValueError("Empty or missing completion content")
    content = choice.message.content
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
        raise ValueError("Empty or missing completion content")
    content = choice.message.content
    model_used = getattr(response, "model", None) or config.azure_openai_model
    return content, model_used
