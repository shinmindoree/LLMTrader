from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BinanceSettings(BaseSettings):
    """바이낸스 API 관련 설정."""

    api_key: str = Field(default="", alias="BINANCE_API_KEY")
    api_secret: str = Field(default="", alias="BINANCE_API_SECRET")
    base_url: str = Field(
        default="https://testnet.binancefuture.com",
        alias="BINANCE_BASE_URL",
        description="테스트넷 기본값. 실서버는 https://fapi.binance.com",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class SlackSettings(BaseSettings):
    """Slack 알림 관련 설정."""

    webhook_url: str = Field(default="", alias="SLACK_WEBHOOK_URL")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class RelayServerSettings(BaseSettings):
    """중계 서버 관련 설정."""

    url: str = Field(
        default="",
        alias="RELAY_SERVER_URL",
        description="LLM 중계 서버 URL",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class Settings(BaseSettings):
    """애플리케이션 전역 설정."""

    env: str = Field(default="local", alias="ENV")
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    slack: SlackSettings = Field(default_factory=SlackSettings)
    relay_server: RelayServerSettings = Field(default_factory=RelayServerSettings)

    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정을 캐싱해 로드한다."""
    return Settings()

