from __future__ import annotations

from api import main as api_main


def test_job_symbols_extracts_unique_live_stream_symbols() -> None:
    assert api_main._job_symbols(
        {
            "streams": [
                {"symbol": "btcusdt"},
                {"symbol": "ETHUSDT"},
                {"symbol": "BTCUSDT"},
                {"interval": "1m"},
            ],
            "symbol": "SOLUSDT",
        }
    ) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_position_amt_accepts_binance_list_response() -> None:
    payload = [
        {"symbol": "ETHUSDT", "positionAmt": "0"},
        {"symbol": "BTCUSDT", "positionAmt": "-0.015"},
    ]

    assert api_main._position_amt(payload, "BTCUSDT") == -0.015


def test_position_amt_defaults_to_zero_for_missing_symbol() -> None:
    assert api_main._position_amt([{"symbol": "ETHUSDT", "positionAmt": "1"}], "BTCUSDT") == 0.0
