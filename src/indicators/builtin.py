"""TA-Lib 기반 builtin 인디케이터 실행기.

설계 목표:
- `LiveContext`/`BacktestContext`가 지표 목록에 강하게 결합되지 않도록,
  builtin 지표는 TA-Lib 함수명을 통해 동적으로 호출한다.
- 전략은 `ctx.get_indicator("RSI", period=14)` 처럼 이름 + 파라미터만 넘기면 된다.
- 커스텀 지표는 `ctx.register_indicator(name, func)`로 별도 등록해서 사용한다.

입력 데이터:
- open/high/low/close/volume 시퀀스를 dict로 전달한다.

출력:
- 단일 output: float (마지막 non-NaN)
- 다중 output: dict[str, float] (각 output의 마지막 non-NaN)
- `output=` 지정 시 해당 output만 float
"""

from __future__ import annotations

import math
import importlib
from typing import Any, Mapping


def _import_talib() -> tuple[Any, Any]:
    """Return (talib, numpy). Raises ImportError when missing."""
    try:
        import numpy as np  # type: ignore
        import talib  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise ImportError(
            "TA-Lib 기반 builtin 인디케이터를 사용하려면 `numpy`와 `TA-Lib`가 필요합니다."
        ) from exc

    # `pip install talib`(다른 패키지) 등으로 인해 `import talib`는 되지만
    # TA-Lib 기능이 없는 경우가 있어, 최소 기능을 검증한다.
    try:
        funcs = list(getattr(talib, "get_functions")())
    except Exception:  # noqa: BLE001
        funcs = []
    if not funcs:
        talib_path = getattr(talib, "__file__", None)
        talib_ver = getattr(talib, "__version__", None)
        raise ImportError(
            "현재 환경의 `talib` 모듈이 TA-Lib wrapper로 동작하지 않습니다. "
            f"(talib_file={talib_path}, talib_version={talib_ver}) "
            "잘못된 패키지(`talib`)가 설치되었을 수 있으니 `TA-Lib` 설치를 확인하세요."
        )
    return talib, np


def _as_float_array(np: Any, values: Any) -> Any:
    return np.asarray(list(values), dtype="float64")


def _last_non_nan(values: Any) -> float | None:
    try:
        n = int(getattr(values, "size", len(values)))
    except Exception:  # noqa: BLE001
        return None
    for i in range(n - 1, -1, -1):
        try:
            v = float(values[i])
        except Exception:  # noqa: BLE001
            continue
        if not math.isnan(v):
            return v
    return None


def compute(
    name: str,
    inputs: Mapping[str, Any],
    *,
    output: str | None = None,
    output_index: int | None = None,
    **params: Any,
) -> Any:
    """Compute TA-Lib indicator by name.

    Args:
        name: TA-Lib indicator name (case-insensitive), e.g. "RSI", "EMA", "MACD"
        inputs: dict with keys like open/high/low/close/volume. Values can be list[float] or numpy arrays.
        output: multi-output indicator의 특정 output 키를 지정.
        output_index: multi-output indicator의 특정 output 인덱스를 지정(0-based).
        **params: TA-Lib 파라미터. 편의를 위해 `period`는 `timeperiod`로 자동 매핑한다.

    Returns:
        float 또는 dict[str, float]
    """
    talib, np = _import_talib()

    if "period" in params and "timeperiod" not in params:
        params["timeperiod"] = params.pop("period")

    normalized_name = name.strip().upper()
    if not normalized_name:
        raise ValueError("indicator name is required")

    try:
        # `talib.abstract`는 submodule이며, 일부 버전에서는 `talib.abstract` 속성이
        # `import talib`만으로는 노출되지 않는다. (hasattr(talib, "abstract") == False)
        abstract = importlib.import_module("talib.abstract")
        fn = abstract.Function(normalized_name)
    except Exception as exc:  # noqa: BLE001
        talib_path = getattr(talib, "__file__", None)
        talib_ver = getattr(talib, "__version__", None)
        try:
            funcs = list(getattr(talib, "get_functions")())
        except Exception:  # noqa: BLE001
            funcs = []
        raise ValueError(
            f"unknown TA-Lib indicator: {name} "
            f"(normalized={normalized_name}, talib_file={talib_path}, talib_version={talib_ver}, "
            f"num_functions={len(funcs)}, talib_has_abstract={hasattr(talib, 'abstract')}, "
            f"exc={type(exc).__name__}: {exc})"
        ) from exc

    prepared_inputs = {
        key: (_as_float_array(np, values) if not hasattr(values, "dtype") else values)
        for key, values in inputs.items()
    }
    # TA-Lib abstract 입력은 indicator별로 "real" 등을 요구할 수 있다.
    # OHLCV 기반 컨텍스트에서 호출하는 사용성을 위해 close -> real alias를 제공한다.
    if "real" not in prepared_inputs and "close" in prepared_inputs:
        prepared_inputs["real"] = prepared_inputs["close"]

    result = fn(prepared_inputs, **params)

    if isinstance(result, dict):
        if output is not None:
            series = result.get(output)
            if series is None:
                raise ValueError(f"unknown output '{output}' for indicator '{normalized_name}'")
            value = _last_non_nan(series)
            return float(value) if value is not None else math.nan

        if output_index is not None:
            keys = list(result.keys())
            if not (0 <= int(output_index) < len(keys)):
                raise ValueError(f"output_index out of range for indicator '{normalized_name}'")
            series = result[keys[int(output_index)]]
            value = _last_non_nan(series)
            return float(value) if value is not None else math.nan

        out: dict[str, float] = {}
        for key, series in result.items():
            value = _last_non_nan(series)
            out[key] = float(value) if value is not None else math.nan
        return out

    if isinstance(result, (list, tuple)):
        if output_index is not None:
            series = result[int(output_index)]
            value = _last_non_nan(series)
            return float(value) if value is not None else math.nan
        first = result[0] if result else None
        value = _last_non_nan(first) if first is not None else None
        return float(value) if value is not None else math.nan

    value = _last_non_nan(result)
    return float(value) if value is not None else math.nan
