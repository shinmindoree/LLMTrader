"""Relay server configuration from environment."""

import os

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RelayConfig(BaseSettings):
    """Environment-based config for the v1 Azure OpenAI relay."""

    azure_tenant_id: str = Field(default="", alias="AZURE_TENANT_ID")
    azure_client_id: str = Field(default="", alias="AZURE_CLIENT_ID")
    azure_client_secret: str = Field(default="", alias="AZURE_CLIENT_SECRET")
    openai_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_BASE_URL", "AZURE_OPENAI_BASE_URL"),
    )
    openai_model: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_MODEL", "AZURE_OPENAI_MODEL"),
    )
    relay_api_key: str = Field(default="", alias="RELAY_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def has_client_secret_credential(self) -> bool:
        return bool(self.azure_tenant_id and self.azure_client_id and self.azure_client_secret)

    @staticmethod
    def _first_env_value(*names: str) -> str:
        for name in names:
            value = os.getenv(name, "").strip()
            if value:
                return value
        return ""

    @property
    def resolved_openai_base_url(self) -> str:
        configured = self.openai_base_url.strip()
        if configured:
            return configured
        return self._first_env_value("OPENAI_BASE_URL", "AZURE_OPENAI_BASE_URL")

    @property
    def resolved_openai_model(self) -> str:
        configured = self.openai_model.strip()
        if configured:
            return configured
        return self._first_env_value("OPENAI_MODEL", "AZURE_OPENAI_MODEL")

    def is_azure_configured(self) -> bool:
        if not self.resolved_openai_model:
            return False
        return bool(self.resolved_openai_base_url)

    def is_api_key_required(self) -> bool:
        return bool(self.relay_api_key)


_config: RelayConfig | None = None


def get_config() -> RelayConfig:
    global _config
    if _config is None:
        _config = RelayConfig()
    return _config
