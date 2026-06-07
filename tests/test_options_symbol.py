"""Options symbol parser/formatter unit tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from options.symbol import (
    OptionSide,
    OptionSymbol,
    format_option_symbol,
    parse_option_symbol,
)


class TestParseOptionSymbol:
    def test_btc_call(self) -> None:
        sym = parse_option_symbol("BTC-241229-100000-C")
        assert sym.asset == "BTC"
        assert sym.expiry == date(2024, 12, 29)
        assert sym.strike == 100_000
        assert sym.side is OptionSide.CALL
        assert sym.underlying == "BTCUSDT"
        assert sym.raw == "BTC-241229-100000-C"

    def test_eth_put(self) -> None:
        sym = parse_option_symbol("ETH-260626-4000-P")
        assert sym.asset == "ETH"
        assert sym.expiry == date(2026, 6, 26)
        assert sym.strike == 4_000
        assert sym.side is OptionSide.PUT
        assert sym.underlying == "ETHUSDT"

    def test_lowercase_input_normalized(self) -> None:
        sym = parse_option_symbol("btc-241229-100000-c")
        assert sym.raw == "BTC-241229-100000-C"
        assert sym.side is OptionSide.CALL

    def test_whitespace_stripped(self) -> None:
        sym = parse_option_symbol("  BTC-241229-100000-C  ")
        assert sym.raw == "BTC-241229-100000-C"

    def test_expiry_ms_is_utc_midnight(self) -> None:
        sym = parse_option_symbol("BTC-241229-100000-C")
        expected = int(datetime(2024, 12, 29, tzinfo=UTC).timestamp() * 1000)
        assert sym.expiry_ms == expected

    def test_days_to_expiry_with_explicit_now(self) -> None:
        sym = parse_option_symbol("BTC-241229-100000-C")
        now = datetime(2024, 12, 24, tzinfo=UTC)
        assert sym.days_to_expiry(now=now) == pytest.approx(5.0, abs=1e-9)

    @pytest.mark.parametrize(
        "bad_symbol",
        [
            "",
            "BTC",
            "BTC-241229-100000",
            "BTC-241229-100000-X",
            "BTC-2412-100000-C",
            "BTC-241229-0-C",
            "BTC-991340-100000-C",
        ],
    )
    def test_invalid_symbols_raise(self, bad_symbol: str) -> None:
        with pytest.raises(ValueError):
            parse_option_symbol(bad_symbol)


class TestFormatOptionSymbol:
    def test_basic_round_trip(self) -> None:
        formatted = format_option_symbol(
            asset="BTC",
            expiry=date(2024, 12, 29),
            strike=100_000,
            side=OptionSide.CALL,
        )
        assert formatted == "BTC-241229-100000-C"
        assert parse_option_symbol(formatted).raw == formatted

    def test_underlying_input_normalized(self) -> None:
        formatted = format_option_symbol(
            asset="BTCUSDT",
            expiry=date(2024, 12, 29),
            strike=100_000,
            side="C",
        )
        assert formatted == "BTC-241229-100000-C"

    def test_datetime_expiry_accepted(self) -> None:
        formatted = format_option_symbol(
            asset="ETH",
            expiry=datetime(2026, 6, 26, 8, 0, tzinfo=UTC),
            strike=4_000,
            side=OptionSide.PUT,
        )
        assert formatted == "ETH-260626-4000-P"

    def test_float_strike_accepted_if_integer(self) -> None:
        formatted = format_option_symbol(
            asset="BTC",
            expiry=date(2024, 12, 29),
            strike=100_000.0,
            side="CALL",
        )
        assert formatted == "BTC-241229-100000-C"

    def test_float_strike_with_fractional_rejected(self) -> None:
        with pytest.raises(ValueError):
            format_option_symbol(
                asset="BTC",
                expiry=date(2024, 12, 29),
                strike=100_000.5,
                side=OptionSide.CALL,
            )

    @pytest.mark.parametrize("side_str", ["C", "c", "CALL", "call"])
    def test_call_aliases(self, side_str: str) -> None:
        sym = format_option_symbol(
            asset="BTC", expiry=date(2024, 12, 29), strike=100_000, side=side_str
        )
        assert sym.endswith("-C")

    @pytest.mark.parametrize("side_str", ["P", "p", "PUT", "put"])
    def test_put_aliases(self, side_str: str) -> None:
        sym = format_option_symbol(
            asset="BTC", expiry=date(2024, 12, 29), strike=100_000, side=side_str
        )
        assert sym.endswith("-P")

    def test_negative_strike_rejected(self) -> None:
        with pytest.raises(ValueError):
            format_option_symbol(
                asset="BTC", expiry=date(2024, 12, 29), strike=-1, side="C"
            )

    def test_invalid_side_rejected(self) -> None:
        with pytest.raises(ValueError):
            format_option_symbol(
                asset="BTC", expiry=date(2024, 12, 29), strike=100_000, side="X"
            )


class TestOptionSymbolModel:
    def test_str_returns_raw(self) -> None:
        sym = OptionSymbol(
            asset="BTC",
            expiry=date(2024, 12, 29),
            strike=100_000,
            side=OptionSide.CALL,
            raw="BTC-241229-100000-C",
        )
        assert str(sym) == "BTC-241229-100000-C"

    def test_underlying_appends_usdt(self) -> None:
        sym = parse_option_symbol("ETH-260626-4000-P")
        assert sym.underlying == "ETHUSDT"

    def test_option_side_from_token_unknown(self) -> None:
        with pytest.raises(ValueError):
            OptionSide.from_token("Z")
