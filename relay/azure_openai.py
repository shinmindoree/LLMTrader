"""Azure OpenAI client using Entra ID (ClientSecretCredential)."""

from __future__ import annotations

from typing import Any

from openai import AzureOpenAI
from azure.identity import ClientSecretCredential, get_bearer_token_provider

from relay.config import RelayConfig


def _create_client(config: RelayConfig) -> AzureOpenAI:
    credential = ClientSecretCredential(
        tenant_id=config.azure_tenant_id,
        client_id=config.azure_client_id,
        client_secret=config.azure_client_secret,
    )
    token_provider = get_bearer_token_provider(
        credential,
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureOpenAI(
        azure_endpoint=config.azure_openai_endpoint.rstrip("/"),
        azure_ad_token_provider=token_provider,
        api_version=config.azure_openai_api_version,
    )


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
