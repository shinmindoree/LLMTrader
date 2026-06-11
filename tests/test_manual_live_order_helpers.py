from __future__ import annotations

import pytest
from fastapi import HTTPException

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


def test_manual_entry_sizing_uses_balance_leverage_and_max_position() -> None:
    sizing = api_main._manual_entry_sizing_from_state(
        config={"streams": [{"symbol": "BTCUSDT", "leverage": 2, "max_position": 0.5}]},
        account={
            "totalMarginBalance": "100",
            "availableBalance": "80",
            "positions": [{"symbol": "BTCUSDT", "positionAmt": "0", "notional": "0"}],
        },
        exchange_filters={"step_size": "0.001", "min_qty": "0.001", "min_notional": "5"},
        symbol="BTCUSDT",
        side="LONG",
        mark_price=20000.0,
    )

    assert sizing["max_notional_usdt"] == 100.0
    assert sizing["max_quantity"] == 0.005


def test_manual_entry_sizing_subtracts_same_direction_position_exposure() -> None:
    sizing = api_main._manual_entry_sizing_from_state(
        config={"streams": [{"symbol": "BTCUSDT", "leverage": 2, "max_position": 0.5}]},
        account={
            "totalMarginBalance": "100",
            "availableBalance": "100",
            "positions": [{"symbol": "BTCUSDT", "positionAmt": "0.003", "notional": "60"}],
        },
        exchange_filters={"step_size": "0.001", "min_qty": "0.001", "min_notional": "5"},
        symbol="BTCUSDT",
        side="LONG",
        mark_price=20000.0,
    )

    assert sizing["max_notional_usdt"] == 40.0
    assert sizing["max_quantity"] == 0.002


def test_manual_entry_sizing_allows_reverse_to_max_position() -> None:
    sizing = api_main._manual_entry_sizing_from_state(
        config={"streams": [{"symbol": "BTCUSDT", "leverage": 2, "max_position": 0.5}]},
        account={
            "totalMarginBalance": "100",
            "availableBalance": "100",
            "positions": [{"symbol": "BTCUSDT", "positionAmt": "-0.003", "notional": "-60"}],
        },
        exchange_filters={"step_size": "0.001", "min_qty": "0.001", "min_notional": "5"},
        symbol="BTCUSDT",
        side="LONG",
        mark_price=20000.0,
    )

    assert sizing["max_notional_usdt"] == 160.0
    assert sizing["max_quantity"] == 0.008


def test_manual_entry_validation_rejects_position_cap_excess() -> None:
    sizing = api_main._manual_entry_sizing_from_state(
        config={"streams": [{"symbol": "BTCUSDT", "leverage": 2, "max_position": 0.5}]},
        account={
            "totalMarginBalance": "100",
            "availableBalance": "100",
            "positions": [{"symbol": "BTCUSDT", "positionAmt": "0", "notional": "0"}],
        },
        exchange_filters={"step_size": "0.001", "min_qty": "0.001", "min_notional": "5"},
        symbol="BTCUSDT",
        side="LONG",
        mark_price=20000.0,
    )

    with pytest.raises(HTTPException):
        api_main._validate_manual_entry_quantity(quantity=0.006, side="LONG", sizing=sizing)
