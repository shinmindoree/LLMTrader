"""Tests for _verify_strategy_code and _verify_strategy_quality in relay/main.py."""

from __future__ import annotations

import textwrap

from relay.main import _verify_init_pattern, _verify_strategy_code, _verify_strategy_quality


# ---------------------------------------------------------------------------
# Good (complete) strategy code — should pass all checks
# ---------------------------------------------------------------------------
GOOD_STRATEGY = textwrap.dedent("""\
    from __future__ import annotations
    import math
    from typing import Any
    from strategy.base import Strategy
    from strategy.context import StrategyContext

    STRATEGY_PARAMS: dict[str, Any] = {
        "rsi_period": 14,
        "entry_rsi": 30.0,
    }

    STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
        "rsi_period": {"type": "integer", "label": "RSI 기간", "group": "지표 (Indicator)"},
        "entry_rsi": {"type": "number", "label": "진입 RSI", "group": "진입 (Entry)"},
    }


    def crossed_above(prev: float, current: float, level: float) -> bool:
        return prev < level <= current


    class TestGoodStrategy(Strategy):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            p = {**STRATEGY_PARAMS, **kwargs}
            self.rsi_period = int(p["rsi_period"])
            self.entry_rsi = float(p["entry_rsi"])
            self.prev_rsi: float | None = None
            self.is_closing: bool = False
            self.indicator_config = {"RSI": {"period": self.rsi_period}}

        def initialize(self, ctx: StrategyContext) -> None:
            self.prev_rsi = None
            self.is_closing = False

        def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
            if ctx.position_size == 0:
                self.is_closing = False
            if ctx.get_open_orders():
                return
            if not bool(bar.get("is_new_bar", True)):
                return
            rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
            if not math.isfinite(rsi):
                return
            if self.prev_rsi is None:
                self.prev_rsi = rsi
                return
            if ctx.position_size == 0:
                if crossed_above(self.prev_rsi, rsi, self.entry_rsi):
                    ctx.enter_long(reason="RSI entry")
            self.prev_rsi = rsi
""")


# ---------------------------------------------------------------------------
# Tests for the original basic checks
# ---------------------------------------------------------------------------

class TestBasicVerification:
    def test_good_strategy_passes(self) -> None:
        assert _verify_strategy_code(GOOD_STRATEGY) is None

    def test_syntax_error(self) -> None:
        code = "def foo(:\n  pass"
        result = _verify_strategy_code(code)
        assert result is not None
        assert "SyntaxError" in result

    def test_no_strategy_class(self) -> None:
        code = textwrap.dedent("""\
            class SomeHelper:
                pass
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "No class ending with 'Strategy' found" in result

    def test_missing_methods(self) -> None:
        code = textwrap.dedent("""\
            from typing import Any
            STRATEGY_PARAMS: dict[str, Any] = {}
            class MyStrategy:
                def __init__(self, **kwargs: Any) -> None:
                    p = {**STRATEGY_PARAMS, **kwargs}
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "missing required methods" in result

    def test_missing_get_open_orders(self) -> None:
        code = textwrap.dedent("""\
            import math
            from typing import Any
            STRATEGY_PARAMS: dict[str, Any] = {"x": 1}
            class MyStrategy:
                def __init__(self, **kwargs: Any) -> None:
                    p = {**STRATEGY_PARAMS, **kwargs}
                    self.is_closing = False
                def initialize(self, ctx): pass
                def on_bar(self, ctx, bar):
                    if not bool(bar.get("is_new_bar", True)): return
                    if math.isfinite(1.0): pass
                    if ctx.position_size == 0: pass
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "get_open_orders" in result

    def test_missing_is_new_bar(self) -> None:
        code = textwrap.dedent("""\
            import math
            from typing import Any
            STRATEGY_PARAMS: dict[str, Any] = {"x": 1}
            class MyStrategy:
                def __init__(self, **kwargs: Any) -> None:
                    p = {**STRATEGY_PARAMS, **kwargs}
                    self.is_closing = False
                def initialize(self, ctx): pass
                def on_bar(self, ctx, bar):
                    if ctx.get_open_orders(): return
                    if math.isfinite(1.0): pass
                    if ctx.position_size == 0: pass
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "is_new_bar" in result


# ---------------------------------------------------------------------------
# Tests for extended quality checks
# ---------------------------------------------------------------------------

class TestQualityVerification:
    def test_missing_strategy_params(self) -> None:
        code = textwrap.dedent("""\
            import math
            from typing import Any
            class MyStrategy:
                def __init__(self, **kwargs: Any) -> None:
                    pass
                def initialize(self, ctx): pass
                def on_bar(self, ctx, bar):
                    if ctx.get_open_orders(): return
                    if not bool(bar.get("is_new_bar", True)): return
                    if math.isfinite(1.0): pass
                    if ctx.position_size == 0: pass
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "STRATEGY_PARAMS" in result

    def test_missing_init(self) -> None:
        code = textwrap.dedent("""\
            import math
            from typing import Any
            STRATEGY_PARAMS: dict[str, Any] = {"x": 1}
            class MyStrategy:
                def initialize(self, ctx): pass
                def on_bar(self, ctx, bar):
                    if ctx.get_open_orders(): return
                    if not bool(bar.get("is_new_bar", True)): return
                    if math.isfinite(1.0): pass
                    if ctx.position_size == 0: pass
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "__init__" in result

    def test_init_without_kwargs(self) -> None:
        code = textwrap.dedent("""\
            import math
            from typing import Any
            STRATEGY_PARAMS: dict[str, Any] = {"x": 1}
            class MyStrategy:
                def __init__(self) -> None:
                    pass
                def initialize(self, ctx): pass
                def on_bar(self, ctx, bar):
                    if ctx.get_open_orders(): return
                    if not bool(bar.get("is_new_bar", True)): return
                    if math.isfinite(1.0): pass
                    if ctx.position_size == 0: pass
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "**kwargs" in result

    def test_init_without_strategy_params_merge(self) -> None:
        code = textwrap.dedent("""\
            import math
            from typing import Any
            STRATEGY_PARAMS: dict[str, Any] = {"x": 1}
            class MyStrategy:
                def __init__(self, **kwargs: Any) -> None:
                    self.x = 1
                def initialize(self, ctx): pass
                def on_bar(self, ctx, bar):
                    if ctx.get_open_orders(): return
                    if not bool(bar.get("is_new_bar", True)): return
                    if math.isfinite(1.0): pass
                    if ctx.position_size == 0: pass
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "STRATEGY_PARAMS" in result

    def test_missing_math_isfinite(self) -> None:
        code = textwrap.dedent("""\
            from typing import Any
            STRATEGY_PARAMS: dict[str, Any] = {"x": 1}
            class MyStrategy:
                def __init__(self, **kwargs: Any) -> None:
                    p = {**STRATEGY_PARAMS, **kwargs}
                def initialize(self, ctx): pass
                def on_bar(self, ctx, bar):
                    if ctx.get_open_orders(): return
                    if not bool(bar.get("is_new_bar", True)): return
                    if ctx.position_size == 0: pass
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "isfinite" in result

    def test_missing_position_size(self) -> None:
        code = textwrap.dedent("""\
            import math
            from typing import Any
            STRATEGY_PARAMS: dict[str, Any] = {"x": 1}
            class MyStrategy:
                def __init__(self, **kwargs: Any) -> None:
                    p = {**STRATEGY_PARAMS, **kwargs}
                def initialize(self, ctx): pass
                def on_bar(self, ctx, bar):
                    if ctx.get_open_orders(): return
                    if not bool(bar.get("is_new_bar", True)): return
                    if math.isfinite(1.0): pass
        """)
        result = _verify_strategy_code(code)
        assert result is not None
        assert "position_size" in result

    def test_isinstance_dict_accepted_for_multi_output(self) -> None:
        """Multi-output indicators (MACD etc.) use isinstance(x, dict) instead of isfinite."""
        code = textwrap.dedent("""\
            from typing import Any
            STRATEGY_PARAMS: dict[str, Any] = {"x": 1}
            class MacdStrategy:
                def __init__(self, **kwargs: Any) -> None:
                    p = {**STRATEGY_PARAMS, **kwargs}
                def initialize(self, ctx): pass
                def on_bar(self, ctx, bar):
                    if ctx.get_open_orders(): return
                    if not bool(bar.get("is_new_bar", True)): return
                    data = ctx.get_indicator("MACD")
                    if not isinstance(data, dict):
                        return
                    if ctx.position_size == 0: pass
        """)
        result = _verify_strategy_code(code)
        assert result is None


# ---------------------------------------------------------------------------
# Test that existing example strategies pass verification
# ---------------------------------------------------------------------------

class TestExistingStrategiesPass:
    def test_rsi_long_short_strategy(self) -> None:
        from pathlib import Path
        strategy_path = (
            Path(__file__).resolve().parent.parent
            / "scripts" / "strategies" / "rsi_long_short_strategy.py"
        )
        if not strategy_path.exists():
            return  # skip if not available
        code = strategy_path.read_text(encoding="utf-8")
        assert _verify_strategy_code(code) is None
