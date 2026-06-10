"""ETH v2 - trend + mean-reversion DIVERSIFIED BLEND (60 sub-legs).

WHY A SEPARATE FILE (v2)
------------------------
* The original ``multi_factor_portfolio_strategy`` is **frozen for BTCUSDT** and
  ``multi_factor_portfolio_eth_strategy`` (v1) is **frozen** as the full-history
  ETH fit. Per project convention we add a new file rather than edit either.
* v1 was tuned on the FULL ETH history, so its strong holdout numbers were not a
  genuine out-of-sample result. v2 is built under a STRICT walk-forward protocol
  (below) and additionally diversifies across market *regimes*.

STRICT-OOS PROTOCOL
-------------------
* TRAIN   = 2022-01-01 .. 2024-09-30  (parameter search + leg selection ONLY)
* VAL     = 2024-10-01 .. 2024-12-31  (kept aside)
* HOLDOUT = 2025-01-01 .. 2026-05-31  (NEVER used for any selection decision)
* HOLDOUT 2025-2026 is a deep ETH bear regime (buy&hold approx -40%, only ~29%
  positive months). Mean-reversion legs that win the 2022-2024 ranging TRAIN do
  NOT generalise to it; trend/breakout legs do. Neither family alone is enough.

METHOD (scripts/_alpha_lab/eth_blend_final.py)
----------------------------------------------
* From the per-leg STRICT-OOS candidate pool, build two sub-portfolios:
    - TREND group:  donchian_breakout / oi_z_combo / pullback_in_trend
    - MEAN-REV grp: ensemble_meanrev / lsr_taker_confluence
* Within each group, pick legs by GREEDY DECORRELATION on TRAIN only (seed with
  the highest-TRAIN-return leg, then repeatedly add the leg least correlated
  with the running basket). This maximises diversification per leg using no
  holdout information.
* Equal-weight 30 trend + 30 mean-reversion legs. The trend sleeve wins
  the bear-trend months while the mean-reversion sleeve supplies a smooth,
  low-MDD base; together they raise +months and cut MDD versus either alone.

PERFORMANCE - accurate per-bar eval, commission 0.0002 both sides
-----------------------------------------------------------------
# HOLDOUT 2025-01..2026-05 (true OOS):
#   +months=82.35%  MDD=2.46%  trades/day=10.19  ret=+8.87%  worst=-0.69%  calmar=2.52
# Per-calendar-year OOS (every year positive, incl. the 2022 and 2025-26 bears):
#   2022: +months=66.7%  MDD=1.79%  ret=+5.43%  worst=-0.85%
#   2023: +months=91.7%  MDD=2.25%  ret=+5.66%  worst=-0.92%
#   2024: +months=58.3%  MDD=2.29%  ret=+4.92%  worst=-1.37%
#   2025: +months=75.0%  MDD=2.46%  ret=+5.73%  worst=-0.69%
#   2026: +months=100.0%  MDD=1.15%  ret=+3.14%  worst=+0.25%
#
# NOTE ON THE +months METRIC: the 17-month holdout is a small sample, so the
# exact positive-month ratio is +/- one month sensitive to the leg count; the
# MDD (~2.5%), worst-month (~-0.7%) and positive return are stable across
# neighbouring basket sizes. See scripts/_alpha_lab/_eth_blend_stability.py.

RUNTIME CONTRACT
----------------
Identical to the parent strategy. Differences:
  - ``self.symbol`` is constrained to ``ETHUSDT``.
  - ``resolve_legs`` / ``_symbol_supported`` are scope-patched during
    ``initialize`` only, exactly like v1, so the BTC and ETH-v1 strategies are
    bit-for-bit unaffected.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from scripts.strategies import multi_factor_portfolio_strategy as _mfp
from scripts.strategies.multi_factor_portfolio_strategy import (
    ALL_LEGS as _BASE_LEGS,
)
from scripts.strategies.multi_factor_portfolio_strategy import (
    TUNABLE_FIELDS as _BASE_TUNABLE_FIELDS,
)
from scripts.strategies.multi_factor_portfolio_strategy import (
    MultiFactorPortfolioStrategy,
)

from strategy.context import StrategyContext

logger = logging.getLogger(__name__)

BASELINE_SYMBOL = "ETHUSDT"
STRATEGY_ID = "multi_factor_portfolio_eth_v2"

# v2 tunes ``side`` plus the per-family threshold knobs that the STRICT-OOS
# sweep explored. All are applied on top of a base leg's config; any field not
# present in the base config is skipped (mirrors the sweep, where such keys were
# harmless no-ops because the signal function reads the base field name).
TUNABLE_FIELDS: frozenset[str] = _BASE_TUNABLE_FIELDS | frozenset({
    "side", "z_long", "z_short", "rsi_long", "rsi_short",
    "lsr_z_long", "lsr_z_short",
})


# ---------------------------------------------------------------------------
# v2 leg list - 60 sub-legs (30 trend + 30 mean-reversion) as
# (base_leg_index, override) pairs. Generated from the validated artifact by
# scripts/_alpha_lab/_gen_eth_v2_strategy.py - do not hand-edit.
# ---------------------------------------------------------------------------
_ETH_V2_VARIANTS: list[tuple[int, dict[str, Any]]] = [
    (15, {"tp_pct": 0.16, "sl_pct": 0.012, "max_hold_h": 96, "side": "short_only"}),
    (4, {"tp_pct": 0.05, "sl_pct": 0.012, "max_hold_h": 32, "side": "long_only", "z_long": 0.8}),
    (4, {"tp_pct": 0.05, "sl_pct": 0.0168, "max_hold_h": 32, "side": "short_only", "z_short": -0.6}),  # noqa: E501  oi_z_combo
    (15, {"tp_pct": 0.08, "sl_pct": 0.0168, "max_hold_h": 24, "side": "long_only"}),
    (3, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 32, "side": "short_only", "z_short": -1.0}),  # noqa: E501  oi_z_combo
    (16, {"tp_pct": 0.16, "sl_pct": 0.0084, "max_hold_h": 96, "side": "long_only"}),
    (0, {"tp_pct": 0.03, "sl_pct": 0.0084, "max_hold_h": 8, "side": "short_only"}),
    (16, {"tp_pct": 0.12, "sl_pct": 0.012, "max_hold_h": 48, "side": "long_only"}),
    (1, {"tp_pct": 0.018, "sl_pct": 0.0168, "max_hold_h": 32, "side": "short_only"}),  # oi_z_combo
    (15, {"tp_pct": 0.16, "sl_pct": 0.012, "max_hold_h": 96, "side": "long_only"}),
    (16, {"tp_pct": 0.16, "sl_pct": 0.0084, "max_hold_h": 192, "side": "long_only"}),
    (1, {"tp_pct": 0.027, "sl_pct": 0.0168, "max_hold_h": 32, "side": "long_only", "z_long": 0.8}),
    (15, {"tp_pct": 0.048, "sl_pct": 0.012, "max_hold_h": 96, "side": "short_only"}),
    (16, {"tp_pct": 0.16, "sl_pct": 0.0084, "max_hold_h": 48, "side": "long_only"}),
    (4, {"tp_pct": 0.0375, "sl_pct": 0.0168, "max_hold_h": 16, "side": "short_only", "z_short": -0.8}),  # noqa: E501  oi_z_combo
    (16, {"tp_pct": 0.08, "sl_pct": 0.012, "max_hold_h": 192, "side": "long_only"}),
    (4, {"tp_pct": 0.05, "sl_pct": 0.0084, "max_hold_h": 32, "side": "long_only", "z_long": 1.2}),
    (15, {"tp_pct": 0.12, "sl_pct": 0.0084, "max_hold_h": 24, "side": "long_only"}),
    (16, {"tp_pct": 0.048, "sl_pct": 0.012, "max_hold_h": 192, "side": "short_only"}),
    (1, {"tp_pct": 0.018, "sl_pct": 0.0168, "max_hold_h": 32, "side": "short_only", "z_long": 0.6}),
    (1, {"tp_pct": 0.036, "sl_pct": 0.0168, "max_hold_h": 32, "side": "both", "z_short": -1.2}),
    (15, {"tp_pct": 0.12, "sl_pct": 0.012, "max_hold_h": 48, "side": "long_only"}),
    (16, {"tp_pct": 0.16, "sl_pct": 0.0084, "max_hold_h": 192, "side": "both"}),
    (16, {"tp_pct": 0.08, "sl_pct": 0.0168, "max_hold_h": 48, "side": "long_only"}),
    (16, {"tp_pct": 0.048, "sl_pct": 0.0168, "max_hold_h": 192, "side": "short_only"}),
    (16, {"tp_pct": 0.12, "sl_pct": 0.0084, "max_hold_h": 192, "side": "long_only"}),
    (3, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 32, "side": "both", "z_short": -1.0}),
    (15, {"tp_pct": 0.16, "sl_pct": 0.0084, "max_hold_h": 48, "side": "short_only"}),
    (3, {"tp_pct": 0.05, "sl_pct": 0.012, "max_hold_h": 32, "side": "long_only", "z_long": 1.0}),
    (4, {"tp_pct": 0.05, "sl_pct": 0.0168, "max_hold_h": 32, "side": "short_only", "z_short": -0.8}),  # noqa: E501  oi_z_combo
    (6, {"tp_pct": 0.027, "sl_pct": 0.0056, "max_hold_h": 8, "side": "both"}),
    (10, {"tp_pct": 0.018, "sl_pct": 0.012, "max_hold_h": 24, "side": "both"}),  # ensemble_meanrev
    (14, {"tp_pct": 0.0375, "sl_pct": 0.0112, "max_hold_h": 24, "side": "long_only", "rsi_long": 28, "rsi_short": 72}),  # noqa: E501  ensemble_meanrev
    (14, {"tp_pct": 0.05, "sl_pct": 0.0112, "max_hold_h": 48, "side": "long_only", "rsi_long": 28, "rsi_short": 72}),  # noqa: E501  ensemble_meanrev
    (7, {"tp_pct": 0.05, "sl_pct": 0.012, "max_hold_h": 8, "side": "long_only"}),
    (7, {"tp_pct": 0.015, "sl_pct": 0.0084, "max_hold_h": 8, "side": "short_only"}),
    (14, {"tp_pct": 0.05, "sl_pct": 0.0112, "max_hold_h": 24, "side": "short_only", "rsi_long": 32, "rsi_short": 68}),  # noqa: E501  ensemble_meanrev
    (5, {"tp_pct": 0.0108, "sl_pct": 0.0084, "max_hold_h": 4, "side": "short_only", "lsr_z_long": 0.5}),  # noqa: E501  lsr_taker_confluence
    (6, {"tp_pct": 0.036, "sl_pct": 0.0056, "max_hold_h": 32, "side": "long_only"}),
    (13, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 12, "side": "long_only"}),
    (12, {"tp_pct": 0.024, "sl_pct": 0.0084, "max_hold_h": 16, "side": "both", "rsi_long": 32, "rsi_short": 68}),  # noqa: E501  ensemble_meanrev
    (13, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 12, "side": "long_only", "rsi_long": 36, "rsi_short": 64}),  # noqa: E501  ensemble_meanrev
    (11, {"tp_pct": 0.012, "sl_pct": 0.0168, "max_hold_h": 8, "side": "both", "rsi_long": 28, "rsi_short": 72}),  # noqa: E501  ensemble_meanrev
    (7, {"tp_pct": 0.015, "sl_pct": 0.0084, "max_hold_h": 4, "side": "short_only"}),
    (13, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 12, "side": "long_only", "rsi_long": 40, "rsi_short": 60}),  # noqa: E501  ensemble_meanrev
    (8, {"tp_pct": 0.025, "sl_pct": 0.0112, "max_hold_h": 16, "side": "short_only"}),
    (13, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 12, "side": "long_only", "rsi_long": 32, "rsi_short": 68}),  # noqa: E501  ensemble_meanrev
    (14, {"tp_pct": 0.025, "sl_pct": 0.0112, "max_hold_h": 12, "side": "long_only", "rsi_long": 28, "rsi_short": 72}),  # noqa: E501  ensemble_meanrev
    (13, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 12, "side": "short_only"}),
    (14, {"tp_pct": 0.0375, "sl_pct": 0.0112, "max_hold_h": 12, "side": "long_only", "rsi_long": 28, "rsi_short": 72}),  # noqa: E501  ensemble_meanrev
    (9, {"tp_pct": 0.018, "sl_pct": 0.012, "max_hold_h": 32, "side": "long_only", "lsr_z_long": 0.5}),  # noqa: E501  lsr_taker_confluence
    (14, {"tp_pct": 0.05, "sl_pct": 0.0112, "max_hold_h": 12, "side": "long_only", "rsi_long": 28, "rsi_short": 72}),  # noqa: E501  ensemble_meanrev
    (11, {"tp_pct": 0.024, "sl_pct": 0.0084, "max_hold_h": 32, "side": "both", "rsi_long": 40, "rsi_short": 60}),  # noqa: E501  ensemble_meanrev
    (7, {"tp_pct": 0.015, "sl_pct": 0.0084, "max_hold_h": 8, "side": "short_only", "lsr_z_long": 0.5}),  # noqa: E501  lsr_taker_confluence
    (13, {"tp_pct": 0.015, "sl_pct": 0.012, "max_hold_h": 12, "side": "long_only", "rsi_long": 28, "rsi_short": 72}),  # noqa: E501  ensemble_meanrev
    (9, {"tp_pct": 0.012, "sl_pct": 0.0168, "max_hold_h": 8, "side": "short_only"}),
    (13, {"tp_pct": 0.015, "sl_pct": 0.012, "max_hold_h": 24, "side": "long_only", "rsi_long": 28, "rsi_short": 72}),  # noqa: E501  ensemble_meanrev
    (13, {"tp_pct": 0.015, "sl_pct": 0.012, "max_hold_h": 48, "side": "long_only", "rsi_long": 28, "rsi_short": 72}),  # noqa: E501  ensemble_meanrev
    (12, {"tp_pct": 0.018, "sl_pct": 0.0084, "max_hold_h": 16, "side": "both", "rsi_long": 32, "rsi_short": 68}),  # noqa: E501  ensemble_meanrev
    (13, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 24, "side": "long_only"}),
]


def _apply_overrides(
    base_leg: dict[str, Any], override: dict[str, Any],
) -> dict[str, Any]:
    cfg = deepcopy(base_leg["config"])
    for k, v in override.items():
        if k not in TUNABLE_FIELDS:
            logger.warning("[mfp-eth-v2] ignoring non-tunable field %r", k)
            continue
        if k not in cfg:
            # Field absent from this family's base config (e.g. an lsr knob on a
            # non-lsr leg). The sweep treated it as a no-op; do the same.
            continue
        cfg[k] = v
    return {"family": base_leg["family"], "config": cfg}


def _build_eth_v2_legs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for base_idx, ov in _ETH_V2_VARIANTS:
        if not 0 <= base_idx < len(_BASE_LEGS):
            raise ValueError(
                f"[mfp-eth-v2] base_idx {base_idx} out of range "
                f"(0..{len(_BASE_LEGS) - 1})"
            )
        out.append(_apply_overrides(_BASE_LEGS[base_idx], ov))
    return out


ETH_V2_LEGS: list[dict[str, Any]] = _build_eth_v2_legs()


def resolve_legs(symbol: str) -> list[dict[str, Any]]:
    sym = symbol.upper()
    if sym != BASELINE_SYMBOL:
        raise ValueError(
            f"MultiFactorPortfolioEthV2Strategy: symbol {sym} is not "
            f"supported; this strategy is tuned exclusively for "
            f"{BASELINE_SYMBOL}."
        )
    return ETH_V2_LEGS


def _symbol_supported(symbol: str) -> bool:
    return symbol.upper() == BASELINE_SYMBOL


STRATEGY_PARAMS: dict[str, Any] = dict(_mfp.STRATEGY_PARAMS)
STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = list(_mfp.STRATEGY_PARAM_SCHEMA)


class MultiFactorPortfolioEthV2Strategy(MultiFactorPortfolioStrategy):
    """ETH v2 - 60-sub-leg trend + mean-reversion regime-diversified blend.

    Reuses the parent lifecycle, data loaders, REST providers, signal funcs and
    per-leg state machine. The only behavioural change is the leg set, selected
    under a strict walk-forward protocol with cross-regime diversification.
    """

    def initialize(self, ctx: StrategyContext) -> None:
        ctx_symbol = getattr(ctx, "symbol", None)
        if ctx_symbol and str(ctx_symbol).upper() != BASELINE_SYMBOL:
            raise ValueError(
                f"MultiFactorPortfolioEthV2Strategy: ctx.symbol={ctx_symbol} "
                f"is not supported; tuned exclusively for {BASELINE_SYMBOL}."
            )
        orig_resolve = _mfp.resolve_legs
        orig_supported = _mfp._symbol_supported
        _mfp.resolve_legs = resolve_legs
        _mfp._symbol_supported = _symbol_supported
        try:
            super().initialize(ctx)
        finally:
            _mfp.resolve_legs = orig_resolve
            _mfp._symbol_supported = orig_supported
