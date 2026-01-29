from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from strategy.base import Strategy


def load_strategy_class(strategy_file: Path) -> type[Strategy]:
    spec = importlib.util.spec_from_file_location("custom_strategy", strategy_file)
    if not spec or not spec.loader:
        raise ValueError(f"Cannot load strategy file: {strategy_file}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_strategy"] = module
    spec.loader.exec_module(module)

    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and name.endswith("Strategy") and obj is not Strategy:
            if issubclass(obj, Strategy):
                return obj
    raise ValueError(f"Strategy class not found in: {strategy_file}")


def build_strategy(strategy_class: type[Strategy], params: dict[str, Any]) -> Strategy:
    if not params:
        return strategy_class()
    try:
        return strategy_class(**params)
    except TypeError as exc:
        raise ValueError(f"Strategy params mismatch: {exc}") from exc

