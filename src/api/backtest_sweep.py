"""Backtest parameter sweep — pure combination logic.

A *sweep* expands a single base backtest config into many child configs by
varying one or more parameters (a grid / cartesian product). This module holds
only the pure, side-effect-free logic so it can be unit tested in isolation.

The actual job creation, persistence and policy checks live in
``src/api/main.py``; a sweep is simply a group of regular ``BACKTEST`` jobs that
share a ``sweep_id`` stored in each job's ``config_json._sweep``.
"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from itertools import product
from typing import Any

__all__ = [
    "MAX_SWEEP_TOTAL_RUNS",
    "MAX_VALUES_PER_DIM",
    "SWEEPABLE_SCALAR_PATHS",
    "SWEEPABLE_CATEGORICAL_PATHS",
    "ALLOWED_INTERVALS",
    "INTEGER_PATHS",
    "SweepError",
    "SweepDimension",
    "build_dimensions",
    "count_runs",
    "expand",
]

# Guardrails to keep a single sweep bounded.
MAX_SWEEP_TOTAL_RUNS = 100
MAX_VALUES_PER_DIM = 50

# Top-level scalar config keys that may be swept.
SWEEPABLE_SCALAR_PATHS: frozenset[str] = frozenset(
    {
        "leverage",
        "initial_balance",
        "commission",
        "slippage_bps",
        "stop_loss_pct",
        "max_position",
        "max_pyramid_entries",
        "fixed_notional",
    }
)

# Categorical (string-valued) config keys that may be swept. Unlike scalar
# paths these only support the ``values`` mode (an explicit list); ``range``
# makes no sense for strings. ``strategy_path`` is special-cased by the API
# layer: each distinct value resolves its own strategy code per child job.
SWEEPABLE_CATEGORICAL_PATHS: frozenset[str] = frozenset(
    {
        "interval",
        "strategy_path",
    }
)

# Allowed candle intervals for the ``interval`` categorical dimension. Mirrors
# ``job_policy._BACKTEST_ALLOWED_INTERVALS``; the per-run policy check is the
# ultimate authority, this set just gives an early, clear preflight error.
ALLOWED_INTERVALS: frozenset[str] = frozenset(
    {
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
        "3d",
        "1w",
    }
)

# Paths whose generated values should be coerced to integers.
INTEGER_PATHS: frozenset[str] = frozenset({"leverage", "max_pyramid_entries"})

_STRATEGY_PARAM_PREFIX = "strategy_params."
_FLOAT_TOLERANCE = 1e-9


class SweepError(ValueError):
    """Raised when a sweep specification is invalid."""


@dataclass(frozen=True)
class SweepDimension:
    """A single swept parameter and its resolved list of values."""

    path: str
    values: list[float | int | str]


def _clean_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SweepError(f"숫자가 아닌 값이 포함되어 있습니다: {value!r}") from exc
    if not math.isfinite(number):
        raise SweepError(f"유한한 숫자가 아닙니다: {value!r}")
    return number


def _round_clean(number: float) -> float:
    """Round away binary float noise (e.g. 0.1 + 0.2) to 10 decimals."""
    return round(number, 10)


def _is_categorical(path: str) -> bool:
    return path in SWEEPABLE_CATEGORICAL_PATHS


def _validate_path(path: str) -> str:
    path = str(path or "").strip()
    if not path:
        raise SweepError("sweep 파라미터 path가 비어 있습니다.")
    if path in SWEEPABLE_SCALAR_PATHS:
        return path
    if path in SWEEPABLE_CATEGORICAL_PATHS:
        return path
    if path.startswith(_STRATEGY_PARAM_PREFIX):
        key = path[len(_STRATEGY_PARAM_PREFIX) :]
        if not key or "." in key:
            raise SweepError(f"지원하지 않는 strategy_params path입니다: {path}")
        return path
    raise SweepError(f"지원하지 않는 sweep 파라미터입니다: {path}")


def _coerce_for_path(path: str, value: float) -> float | int:
    if path in INTEGER_PATHS:
        return int(round(value))
    return _round_clean(value)


def _values_from_range(path: str, start: Any, end: Any, step: Any) -> list[float | int]:
    start_f = _clean_number(start)
    end_f = _clean_number(end)
    step_f = _clean_number(step)
    if step_f <= 0:
        raise SweepError(f"step은 0보다 커야 합니다 ({path}: step={step_f}).")
    if end_f < start_f:
        raise SweepError(f"end는 start보다 같거나 커야 합니다 ({path}).")

    count = int(math.floor((end_f - start_f) / step_f + _FLOAT_TOLERANCE)) + 1
    if count > MAX_VALUES_PER_DIM:
        raise SweepError(
            f"{path}: 한 파라미터당 값은 최대 {MAX_VALUES_PER_DIM}개까지 가능합니다 "
            f"(현재 {count}개)."
        )

    values: list[float | int] = []
    seen: set[float] = set()
    for i in range(count):
        raw = start_f + i * step_f
        coerced = _coerce_for_path(path, raw)
        key = float(coerced)
        if key in seen:
            continue
        seen.add(key)
        values.append(coerced)
    return values


def _values_from_list(path: str, raw_values: Any) -> list[float | int]:
    if not isinstance(raw_values, (list, tuple)) or not raw_values:
        raise SweepError(f"{path}: values 리스트가 비어 있습니다.")
    values: list[float | int] = []
    seen: set[float] = set()
    for raw in raw_values:
        coerced = _coerce_for_path(path, _clean_number(raw))
        key = float(coerced)
        if key in seen:
            continue
        seen.add(key)
        values.append(coerced)
    if len(values) > MAX_VALUES_PER_DIM:
        raise SweepError(
            f"{path}: 한 파라미터당 값은 최대 {MAX_VALUES_PER_DIM}개까지 가능합니다 "
            f"(현재 {len(values)}개)."
        )
    return values


def _categorical_values_from_list(path: str, raw_values: Any) -> list[str]:
    if not isinstance(raw_values, (list, tuple)) or not raw_values:
        raise SweepError(f"{path}: values 리스트가 비어 있습니다.")
    values: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        text = str(raw).strip()
        if not text:
            raise SweepError(f"{path}: 빈 값은 사용할 수 없습니다.")
        if path == "interval":
            text = text.lower()
            if text not in ALLOWED_INTERVALS:
                raise SweepError(
                    f"interval: 지원하지 않는 캔들주기입니다 ({text}). "
                    f"허용값={sorted(ALLOWED_INTERVALS)}"
                )
        if text in seen:
            continue
        seen.add(text)
        values.append(text)
    if len(values) > MAX_VALUES_PER_DIM:
        raise SweepError(
            f"{path}: 한 파라미터당 값은 최대 {MAX_VALUES_PER_DIM}개까지 가능합니다 "
            f"(현재 {len(values)}개)."
        )
    return values


def build_dimensions(specs: list[dict[str, Any]]) -> list[SweepDimension]:
    """Validate raw dimension specs and resolve each to a list of values.

    Each spec is a dict with ``path``, ``mode`` ("range" | "values") and either
    ``start``/``end``/``step`` or ``values``. Categorical paths
    (``interval``, ``strategy_path``) accept only ``values`` mode.
    """
    if not specs:
        raise SweepError("최소 1개의 sweep 파라미터가 필요합니다.")

    dimensions: list[SweepDimension] = []
    seen_paths: set[str] = set()
    for spec in specs:
        if not isinstance(spec, dict):
            raise SweepError("sweep 파라미터 형식이 올바르지 않습니다.")
        path = _validate_path(spec.get("path", ""))
        if path in seen_paths:
            raise SweepError(f"중복된 sweep 파라미터입니다: {path}")
        seen_paths.add(path)

        mode = str(spec.get("mode") or "").strip().lower()
        values: list[float | int | str]
        if _is_categorical(path):
            if mode != "values":
                raise SweepError(
                    f"{path}: 범주형 파라미터는 values 모드만 지원합니다 (range 불가)."
                )
            values = list(_categorical_values_from_list(path, spec.get("values")))
        elif mode == "range":
            values = list(
                _values_from_range(
                    path, spec.get("start"), spec.get("end"), spec.get("step")
                )
            )
        elif mode == "values":
            values = list(_values_from_list(path, spec.get("values")))
        else:
            raise SweepError(f"지원하지 않는 mode입니다: {mode!r} (range|values)")

        if not values:
            raise SweepError(f"{path}: 생성된 값이 없습니다.")
        dimensions.append(SweepDimension(path=path, values=values))

    total = count_runs(dimensions)
    if total > MAX_SWEEP_TOTAL_RUNS:
        raise SweepError(
            f"총 실행 수가 너무 많습니다 ({total}개). 최대 {MAX_SWEEP_TOTAL_RUNS}개까지 "
            "가능합니다. 범위를 줄이세요."
        )
    return dimensions


def count_runs(dimensions: list[SweepDimension]) -> int:
    total = 1
    for dim in dimensions:
        total *= len(dim.values)
    return total


def _set_path(config: dict[str, Any], path: str, value: float | int | str) -> None:
    if path.startswith(_STRATEGY_PARAM_PREFIX):
        key = path[len(_STRATEGY_PARAM_PREFIX) :]
        sub = config.get("strategy_params")
        if not isinstance(sub, dict):
            sub = {}
            config["strategy_params"] = sub
        sub[key] = value
    else:
        config[path] = value


def expand(
    base_config: dict[str, Any], dimensions: list[SweepDimension]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Expand ``base_config`` across every combination of ``dimensions``.

    Returns a list of ``(varied_params, full_config)`` tuples where
    ``varied_params`` maps the swept path to the value used for that run.
    """
    axes = [[(dim.path, value) for value in dim.values] for dim in dimensions]
    expanded: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for combo in product(*axes):
        varied: dict[str, Any] = {}
        config = deepcopy(base_config)
        for path, value in combo:
            varied[path] = value
            _set_path(config, path, value)
        expanded.append((varied, config))
    return expanded
