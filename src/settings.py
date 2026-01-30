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
    """LLM 중계(프록시) 서버 설정."""

    url: str = Field(default="", alias="RELAY_SERVER_URL")
    api_key: str = Field(default="", alias="RELAY_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class Settings(BaseSettings):
    """애플리케이션 전역 설정."""

    env: str = Field(default="local", alias="ENV")
    database_url: str = Field(
        default="postgresql+asyncpg://llmtrader:llmtrader@localhost:5432/llmtrader",
        alias="DATABASE_URL",
    )
    admin_token: str = Field(default="dev-admin-token", alias="ADMIN_TOKEN")
    strategy_dirs: str = Field(default="scripts/strategies", alias="STRATEGY_DIRS")
    runner_poll_interval_ms: int = Field(default=500, alias="RUNNER_POLL_INTERVAL_MS")
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    slack: SlackSettings = Field(default_factory=SlackSettings)
    relay_server: RelayServerSettings = Field(default_factory=RelayServerSettings)

    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정을 캐싱해 로드한다."""
    return Settings()
