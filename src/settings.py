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


class NextAuthSettings(BaseSettings):
    """NextAuth.js 인증 설정 (shared secret 기반)."""

    secret: str = Field(default="", alias="AUTH_SECRET", repr=False)
    enabled: bool = Field(default=False, alias="NEXTAUTH_ENABLED")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class EntraAuthSettings(BaseSettings):
    """Microsoft Entra ID 인증 설정."""

    enabled: bool = Field(default=False, alias="ENTRA_AUTH_ENABLED")
    tenant_id: str = Field(default="", alias="ENTRA_TENANT_ID")
    client_id: str = Field(default="", alias="ENTRA_CLIENT_ID")
    authority: str = Field(default="", alias="ENTRA_AUTHORITY")
    allow_admin_fallback: bool = Field(default=True, alias="AUTH_ALLOW_ADMIN_TOKEN_FALLBACK")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def issuer(self) -> str:
        if self.authority:
            return f"{self.authority.rstrip('/')}/v2.0"
        if self.tenant_id:
            return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        return ""

    @property
    def jwks_uri(self) -> str:
        if self.authority:
            return f"{self.authority.rstrip('/')}/discovery/v2.0/keys"
        if self.tenant_id:
            return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
        return ""


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


class AcsEmailSettings(BaseSettings):
    """Azure Communication Services Email 설정."""

    connection_string: str = Field(default="", alias="ACS_EMAIL_CONNECTION_STRING", repr=False)
    sender_address: str = Field(default="", alias="ACS_EMAIL_SENDER_ADDRESS")
    sender_name: str = Field(default="AlphaWeaver", alias="ACS_EMAIL_SENDER_NAME")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_configured(self) -> bool:
        return bool(self.connection_string and self.sender_address)


class RedisSettings(BaseSettings):
    """Redis 캐시 설정."""

    url: str = Field(default="", alias="REDIS_URL")
    kline_cache_ttl: int = Field(default=300, alias="REDIS_KLINE_CACHE_TTL")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_configured(self) -> bool:
        return bool(self.url.strip())


class AzureBlobSettings(BaseSettings):
    """Azure Blob Storage 설정."""

    account_url: str = Field(default="", alias="AZURE_BLOB_ACCOUNT_URL")
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
    admin_token: str = Field(default="dev-admin-token", alias="ADMIN_TOKEN")
    admin_email: str = Field(default="elgd00@gmail.com", alias="ADMIN_EMAIL")
    strategy_dirs: str = Field(default="scripts/strategies", alias="STRATEGY_DIRS")
    runner_poll_interval_ms: int = Field(default=500, alias="RUNNER_POLL_INTERVAL_MS")
    runner_live_concurrency: int = Field(default=5, alias="RUNNER_LIVE_CONCURRENCY")
    runner_live_heartbeat_interval_sec: int = Field(default=30, alias="RUNNER_LIVE_HEARTBEAT_INTERVAL_SEC")
    runner_stale_live_seconds: int = Field(default=120, alias="RUNNER_STALE_LIVE_SECONDS")
    runner_live_initial_heartbeat_grace_sec: int = Field(
        default=180, alias="RUNNER_LIVE_INITIAL_HEARTBEAT_GRACE_SEC"
    )
    runner_periodic_reconcile_interval_sec: int = Field(
        default=45, alias="RUNNER_PERIODIC_RECONCILE_INTERVAL_SEC"
    )
    embedded_runner: bool = Field(default=True, alias="EMBEDDED_RUNNER")
    auto_alembic_upgrade: bool = Field(default=True, alias="AUTO_ALEMBIC_UPGRADE")
    frontend_url: str = Field(default="http://localhost:3000", alias="FRONTEND_URL")
    crypto_backend: str = Field(default="fernet", alias="CRYPTO_BACKEND")

    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    slack: SlackSettings = Field(default_factory=SlackSettings)
    encryption: EncryptionSettings = Field(default_factory=EncryptionSettings)
    stripe: StripeSettings = Field(default_factory=StripeSettings)
    entra_auth: EntraAuthSettings = Field(default_factory=EntraAuthSettings)
    nextauth: NextAuthSettings = Field(default_factory=NextAuthSettings)
    azure_blob: AzureBlobSettings = Field(default_factory=AzureBlobSettings)
    azure_keyvault: AzureKeyVaultSettings = Field(default_factory=AzureKeyVaultSettings)
    acs_email: AcsEmailSettings = Field(default_factory=AcsEmailSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def effective_database_url(self) -> str:
        explicit = self.database_url.strip()
        if explicit:
            return explicit
        return "postgresql+asyncpg://llmtrader:llmtrader@localhost:5432/llmtrader"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
