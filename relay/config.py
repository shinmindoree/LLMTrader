"""Relay server configuration from environment."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RelayConfig(BaseSettings):
    """Environment-based config for the LLM relay (Azure OpenAI proxy)."""

    azure_tenant_id: str = Field(default="", alias="AZURE_TENANT_ID")
    azure_client_id: str = Field(default="", alias="AZURE_CLIENT_ID")
    azure_client_secret: str = Field(default="", alias="AZURE_CLIENT_SECRET")
    azure_openai_endpoint: str = Field(default="", alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_model: str = Field(default="", alias="AZURE_OPENAI_MODEL")
    azure_openai_api_version: str = Field(
        default="2024-08-01-preview",
        alias="AZURE_OPENAI_API_VERSION",
    )
    relay_api_key: str = Field(default="", alias="RELAY_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def is_azure_configured(self) -> bool:
        return bool(
            self.azure_tenant_id
            and self.azure_client_id
            and self.azure_client_secret
            and self.azure_openai_endpoint
            and self.azure_openai_model
        )

    def is_api_key_required(self) -> bool:
        return bool(self.relay_api_key)


_config: RelayConfig | None = None


def get_config() -> RelayConfig:
    global _config
    if _config is None:
        _config = RelayConfig()
    return _config
