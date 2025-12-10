import os

from llmtrader.settings import BinanceSettings, Settings, get_settings


def test_settings_default_env_local() -> None:
    settings = Settings()
    assert settings.env == "local"


def test_binance_settings_override_via_env() -> None:
    os.environ["BINANCE_API_KEY"] = "key"
    os.environ["BINANCE_API_SECRET"] = "secret"
    os.environ["BINANCE_BASE_URL"] = "https://example.com"

    binance = BinanceSettings()
    assert binance.api_key == "key"
    assert binance.api_secret == "secret"
    assert binance.base_url == "https://example.com"

    # cleanup
    os.environ.pop("BINANCE_API_KEY")
    os.environ.pop("BINANCE_API_SECRET")
    os.environ.pop("BINANCE_BASE_URL")


def test_get_settings_cached() -> None:
    first = get_settings()
    second = get_settings()
    assert first is second




