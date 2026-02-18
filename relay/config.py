"""Relay server configuration from environment."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RelayConfig(BaseSettings):
    """Environment-based config for the LLM relay (Azure OpenAI proxy)."""

    azure_tenant_id: str = Field(default="", alias="AZURE_TENANT_ID")
    azure_client_id: str = Field(default="", alias="AZURE_CLIENT_ID")
    azure_client_secret: str = Field(default="", alias="AZURE_CLIENT_SECRET")
    azure_ai_project_endpoint: str = Field(default="", alias="AZURE_AI_PROJECT_ENDPOINT")
    azure_ai_project_connection_name: str = Field(
        default="",
        alias="AZURE_AI_PROJECT_CONNECTION_NAME",
    )
    azure_ai_project_openai_api_version: str = Field(
        default="",
        alias="AZURE_AI_PROJECT_OPENAI_API_VERSION",
    )
    azure_openai_endpoint: str = Field(default="", alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_model: str = Field(default="", alias="AZURE_OPENAI_MODEL")
    azure_openai_api_version: str = Field(
        default="2024-08-01-preview",
        alias="AZURE_OPENAI_API_VERSION",
    )
    relay_api_key: str = Field(default="", alias="RELAY_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def has_client_secret_credential(self) -> bool:
        return bool(self.azure_tenant_id and self.azure_client_id and self.azure_client_secret)

    def is_foundry_project_mode(self) -> bool:
        return bool(self.azure_ai_project_endpoint)

    def is_azure_configured(self) -> bool:
        if not self.azure_openai_model:
            return False
        if self.is_foundry_project_mode():
            return True
        # Endpoint/model are required for direct Azure OpenAI endpoint mode.
        # Auth is resolved by:
        # 1) Client secret tuple (tenant/client/secret) or
        # 2) DefaultAzureCredential chain (managed identity, az login, etc).
        return bool(self.azure_openai_endpoint)

    def is_api_key_required(self) -> bool:
        return bool(self.relay_api_key)


_config: RelayConfig | None = None


def get_config() -> RelayConfig:
    global _config
    if _config is None:
        _config = RelayConfig()
    return _config
