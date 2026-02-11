from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BinanceSettings(BaseSettings):
    """바이낸스 API 관련 설정 (글로벌 폴백 — 사용자별 키가 없을 때 사용)."""

    api_key: str = Field(default="", alias="BINANCE_API_KEY")
    api_secret: str = Field(default="", alias="BINANCE_API_SECRET")
    base_url: str = Field(
        default="https://testnet.binancefuture.com",
        alias="BINANCE_BASE_URL",
        description="테스트넷 기본값. 실서버는 https://fapi.binance.com",
    )
    base_url_backtest: str = Field(
        default="",
        alias="BINANCE_BASE_URL_BACKTEST",
        description="백테스트 전용 바이낸스 엔드포인트.",
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


class SupabaseAuthSettings(BaseSettings):
    """Supabase 인증 설정."""

    enabled: bool = Field(default=False, alias="SUPABASE_AUTH_ENABLED")
    url: str = Field(default="", alias="SUPABASE_URL")
    anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")
    auth_timeout_seconds: float = Field(default=5.0, alias="SUPABASE_AUTH_TIMEOUT_SECONDS")
    allow_admin_fallback: bool = Field(default=True, alias="AUTH_ALLOW_ADMIN_TOKEN_FALLBACK")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class EncryptionSettings(BaseSettings):
    """암호화 키 설정. 쉼표 구분 Fernet 키 목록 (첫번째=현재, 나머지=이전)."""

    keys: str = Field(default="", alias="ENCRYPTION_KEYS", repr=False)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def key_list(self) -> list[str]:
        raw = self.keys.strip()
        if not raw:
            return []
        return [k.strip() for k in raw.split(",") if k.strip()]


class StripeSettings(BaseSettings):
    """Stripe 결제 설정."""

    secret_key: str = Field(default="", alias="STRIPE_SECRET_KEY", repr=False)
    webhook_secret: str = Field(default="", alias="STRIPE_WEBHOOK_SECRET", repr=False)
    price_id_pro: str = Field(default="", alias="STRIPE_PRICE_ID_PRO")
    price_id_enterprise: str = Field(default="", alias="STRIPE_PRICE_ID_ENTERPRISE")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AzureBlobSettings(BaseSettings):
    """Azure Blob Storage 설정."""

    connection_string: str = Field(default="", alias="AZURE_BLOB_CONNECTION_STRING", repr=False)
    container_name: str = Field(default="strategies", alias="AZURE_BLOB_CONTAINER_NAME")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AzureKeyVaultSettings(BaseSettings):
    """Azure Key Vault 설정."""

    url: str = Field(default="", alias="AZURE_KEYVAULT_URL")
    key_name: str = Field(default="llmtrader-encryption-key", alias="AZURE_KEYVAULT_KEY_NAME")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class Settings(BaseSettings):
    """애플리케이션 전역 설정."""

    env: str = Field(default="local", alias="ENV")
    database_url: str = Field(default="", alias="DATABASE_URL")
    supabase_database_url: str = Field(default="", alias="SUPABASE_DATABASE_URL")
    admin_token: str = Field(default="dev-admin-token", alias="ADMIN_TOKEN")
    strategy_dirs: str = Field(default="scripts/strategies", alias="STRATEGY_DIRS")
    runner_poll_interval_ms: int = Field(default=500, alias="RUNNER_POLL_INTERVAL_MS")
    frontend_url: str = Field(default="http://localhost:3000", alias="FRONTEND_URL")
    crypto_backend: str = Field(default="fernet", alias="CRYPTO_BACKEND")

    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    slack: SlackSettings = Field(default_factory=SlackSettings)
    relay_server: RelayServerSettings = Field(default_factory=RelayServerSettings)
    supabase_auth: SupabaseAuthSettings = Field(default_factory=SupabaseAuthSettings)
    encryption: EncryptionSettings = Field(default_factory=EncryptionSettings)
    stripe: StripeSettings = Field(default_factory=StripeSettings)
    azure_blob: AzureBlobSettings = Field(default_factory=AzureBlobSettings)
    azure_keyvault: AzureKeyVaultSettings = Field(default_factory=AzureKeyVaultSettings)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def effective_database_url(self) -> str:
        explicit = self.database_url.strip()
        if explicit:
            return explicit
        supabase = self.supabase_database_url.strip()
        if supabase:
            return supabase
        return "postgresql+asyncpg://llmtrader:llmtrader@localhost:5432/llmtrader"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
