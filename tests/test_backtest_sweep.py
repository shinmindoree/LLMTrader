"""Unit tests for the pure backtest sweep combination logic."""

from __future__ import annotations

import pytest

from api.backtest_sweep import (
    MAX_SWEEP_TOTAL_RUNS,
    MAX_VALUES_PER_DIM,
    SweepError,
    build_dimensions,
    count_runs,
    expand,
)


def test_range_inclusive_integer_leverage():
    dims = build_dimensions(
        [{"path": "leverage", "mode": "range", "start": 1, "end": 10, "step": 1}]
    )
    assert len(dims) == 1
    assert dims[0].values == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert all(isinstance(v, int) for v in dims[0].values)


def test_range_float_step_no_binary_noise():
    dims = build_dimensions(
        [{"path": "stop_loss_pct", "mode": "range", "start": 0.01, "end": 0.05, "step": 0.01}]
    )
    assert dims[0].values == [0.01, 0.02, 0.03, 0.04, 0.05]


def test_values_mode_dedup_and_order():
    dims = build_dimensions(
        [{"path": "leverage", "mode": "values", "values": [3, 3, 1, 2]}]
    )
    assert dims[0].values == [3, 1, 2]


def test_cartesian_expansion():
    dims = build_dimensions(
        [
            {"path": "leverage", "mode": "values", "values": [1, 2]},
            {"path": "stop_loss_pct", "mode": "values", "values": [0.01, 0.02]},
        ]
    )
    assert count_runs(dims) == 4
    expanded = expand({"symbol": "BTCUSDT", "leverage": 1}, dims)
    assert len(expanded) == 4
    varied_sets = [v for v, _ in expanded]
    assert {"leverage": 1, "stop_loss_pct": 0.01} in varied_sets
    assert {"leverage": 2, "stop_loss_pct": 0.02} in varied_sets
    # base keys preserved, swept keys overridden
    for varied, cfg in expanded:
        assert cfg["symbol"] == "BTCUSDT"
        assert cfg["leverage"] == varied["leverage"]
        assert cfg["stop_loss_pct"] == varied["stop_loss_pct"]


def test_strategy_params_nested_path():
    dims = build_dimensions(
        [{"path": "strategy_params.rsi_period", "mode": "values", "values": [7, 14]}]
    )
    expanded = expand({"symbol": "BTCUSDT", "strategy_params": {"src": "close"}}, dims)
    assert len(expanded) == 2
    cfgs = [cfg for _, cfg in expanded]
    assert cfgs[0]["strategy_params"] == {"src": "close", "rsi_period": 7}
    assert cfgs[1]["strategy_params"] == {"src": "close", "rsi_period": 14}
    # base config is not mutated across runs
    assert cfgs[0]["strategy_params"] is not cfgs[1]["strategy_params"]


def test_zero_step_rejected():
    with pytest.raises(SweepError):
        build_dimensions(
            [{"path": "leverage", "mode": "range", "start": 1, "end": 10, "step": 0}]
        )


def test_end_before_start_rejected():
    with pytest.raises(SweepError):
        build_dimensions(
            [{"path": "leverage", "mode": "range", "start": 10, "end": 1, "step": 1}]
        )


def test_unsupported_path_rejected():
    with pytest.raises(SweepError):
        build_dimensions(
            [{"path": "symbol", "mode": "values", "values": [1, 2]}]
        )


def test_duplicate_path_rejected():
    with pytest.raises(SweepError):
        build_dimensions(
            [
                {"path": "leverage", "mode": "values", "values": [1, 2]},
                {"path": "leverage", "mode": "values", "values": [3, 4]},
            ]
        )


def test_per_dimension_limit_enforced():
    with pytest.raises(SweepError):
        build_dimensions(
            [
                {
                    "path": "leverage",
                    "mode": "range",
                    "start": 1,
                    "end": MAX_VALUES_PER_DIM + 5,
                    "step": 1,
                }
            ]
        )


def test_total_runs_limit_enforced():
    # 10 x 11 = 110 > MAX_SWEEP_TOTAL_RUNS (100)
    with pytest.raises(SweepError):
        build_dimensions(
            [
                {"path": "leverage", "mode": "range", "start": 1, "end": 10, "step": 1},
                {"path": "max_pyramid_entries", "mode": "range", "start": 0, "end": 10, "step": 1},
            ]
        )


def test_empty_specs_rejected():
    with pytest.raises(SweepError):
        build_dimensions([])


def test_total_runs_limit_constant_sane():
    assert MAX_SWEEP_TOTAL_RUNS >= 10
