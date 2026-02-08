"""Azure OpenAI client using Entra ID credentials.

Auth strategy:
1) Use ClientSecretCredential when tenant/client/secret env vars are provided.
2) Otherwise use DefaultAzureCredential (Managed Identity preferred in Azure runtime).
"""

from __future__ import annotations

from typing import AsyncIterator

from openai import AsyncAzureOpenAI, AzureOpenAI
from azure.core.credentials import TokenCredential
from azure.identity import ClientSecretCredential, DefaultAzureCredential, get_bearer_token_provider

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


def _create_client(config: RelayConfig) -> AzureOpenAI:
    credential = _build_credential(config)
    token_provider = get_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureOpenAI(
        azure_endpoint=config.azure_openai_endpoint.rstrip("/"),
        azure_ad_token_provider=token_provider,
        api_version=config.azure_openai_api_version,
    )


def _create_async_client(config: RelayConfig) -> AsyncAzureOpenAI:
    credential = _build_credential(config)
    token_provider = get_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default",
    )
    return AsyncAzureOpenAI(
        azure_endpoint=config.azure_openai_endpoint.rstrip("/"),
        azure_ad_token_provider=token_provider,
        api_version=config.azure_openai_api_version,
    )


async def chat_completion_stream(
    config: RelayConfig,
    system_content: str,
    user_content: str | None = None,
    messages: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    """Stream Azure OpenAI Chat Completions; yield content deltas."""
    client = _create_async_client(config)
    if messages:
        full_messages = [{"role": "system", "content": system_content}] + messages
    else:
        full_messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content or ""},
        ]
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
    """Call Azure OpenAI Chat Completions. Returns (content, model_used)."""
    client = _create_client(config)
    response = client.chat.completions.create(
        model=config.azure_openai_model,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
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
    """Call Azure OpenAI Chat Completions with multi-turn messages. Returns (content, model_used)."""
    client = _create_client(config)
    full_messages = [{"role": "system", "content": system_content}] + messages
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
