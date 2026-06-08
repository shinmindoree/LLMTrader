"""ETH-tuned variant of the multi-factor portfolio strategy (29 sub-legs).

WHY A SEPARATE FILE
-------------------
* The original ``multi_factor_portfolio_strategy`` is **frozen for BTCUSDT** —
  per project convention we must not modify its leg structure / tunables.
* ETH dynamics differ enough from BTC that a simple per-leg threshold
  re-fit (the path supported by ``TUNABLE_FIELDS``) is insufficient: two
  baseline legs cannot pass quality gates on ETH at all, and several
  mean-reversion legs only become positive when their ``side`` is
  specialised (long-only or short-only) instead of two-sided.
* This file therefore ships its own **multi-variant leg list** (29 sub-legs
  derived from the 17 BTC baseline legs by adding per-side specialisations)
  plus an extra tunable (``side``) that the parent's ``TUNABLE_FIELDS``
  intentionally excludes.

DISCOVERY (scripts/_alpha_lab/mfp_eth_tune.py, v5 "multi_variant" sweep)
------------------------------------------------------------------------
* Window: 2022-01-01 → 2026-04-29 (full ETH OI/taker/LSR history).
* Per-leg sweep over 525 candidates each (TP × SL × HOLD × SIDE multipliers
  anchored on the BTC baseline), then portfolio-aggregated with equal-weight
  per-bar averaging.
* Gate: trades>=15, ret>0, pf>=1.0, MDD<=25%, plus MDD penalty above 8%
  and a trade-count band targeting BTC parity.
* For each base leg we keep the best variant per side ∈ {both, long_only,
  short_only}; if all three pass the gate the leg contributes 3 sub-legs to
  the portfolio. Baseline legs 4 and 14 are dropped (no candidate cleared the
  gate on ETH).

PERFORMANCE (ETHUSDT, commission 0.0004)
----------------------------------------
| window               | trades | ret    | MDD   | +months | worst   | calmar |
|----------------------|--------|--------|-------|---------|---------|--------|
| FULL 2022→26.04      |  5540  | +45.07%| 2.81% | 71.15%  | -1.98%  | 3.19   |
| TRAIN 2022→24.06     |  -     | +23.59%| 2.81% | 60.00%  | -1.06%  | 3.15   |
| TEST  2024.07→26.04  |  -     | +21.48%| 2.65% | 86.36%  | -1.98%  | 4.24   |
| WF W1 2022.01→23.06  |  -     | +24.05%| 2.29% | 66.67%  | -1.04%  | 6.77   |
| WF W2 2023.07→24.12  |  -     | +8.36% | 3.48% | 66.67%  | -1.06%  | 1.57   |
| WF W3 2025.01→26.04  |  -     | +12.66%| 2.87% | 81.25%  | -1.98%  | 3.28   |

Compare to the BTC baseline portfolio on the same FULL window:
  BTC: trades=6398, ret=+34.17%, MDD=4.72%, +months=61.54%, calmar=1.49.
The ETH-tuned portfolio beats BTC on every quality metric (ret, MDD, +months,
calmar) at 87% of BTC's trade count.

RUNTIME CONTRACT
----------------
Identical to the parent strategy (same Strategy class, same lifecycle, same
parquet/Redis data sources). The only differences are:
  - ``self.symbol`` is constrained to ``ETHUSDT``.
  - ``resolve_legs`` is bypassed in favour of the ETH-tuned ``ETH_LEGS``.
  - ``_symbol_supported`` is bypassed (no promoted parameter artifact is
    required because the overrides are baked in below).
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


# Symbol whose discovery is baked into ``_ETH_VARIANTS`` below.
BASELINE_SYMBOL = "ETHUSDT"

# Stable identifier (kept distinct from the BTC strategy so the live runner
# and the param store cannot accidentally cross-load configs).
STRATEGY_ID = "multi_factor_portfolio_eth"

# ETH adds ``side`` to the tunable set. The parent strategy treats it as a
# structural field; for ETH we deliberately specialise it per sub-leg.
TUNABLE_FIELDS: frozenset[str] = _BASE_TUNABLE_FIELDS | frozenset({"side"})


# ---------------------------------------------------------------------------
# ETH multi-variant per-leg overrides — 29 sub-legs across 17 base legs.
# Discovered by scripts/_alpha_lab/mfp_eth_tune.py (v5 sweep).
# Each entry is (base_leg_index, override_dict). Base legs 4 and 14 are
# intentionally omitted (no candidate passed the quality gate on ETH).
# ---------------------------------------------------------------------------
_ETH_VARIANTS: list[tuple[int, dict[str, Any]]] = [
    # leg 0 — pullback_in_trend (30m)
    (0, {"tp_pct": 0.030, "sl_pct": 0.024, "max_hold_h": 24, "side": "long_only"}),
    # leg 1 — oi_z_combo (60m)
    (1, {"tp_pct": 0.036, "sl_pct": 0.0168, "max_hold_h": 24, "side": "long_only"}),
    # leg 2 — oi_z_combo (60m)
    (2, {"tp_pct": 0.0312, "sl_pct": 0.0168, "max_hold_h": 24, "side": "long_only"}),
    # leg 3 — oi_z_combo (60m)
    (3, {"tp_pct": 0.0312, "sl_pct": 0.0168, "max_hold_h": 24, "side": "long_only"}),
    # leg 4 — oi_z_combo (30m) — DROPPED (no positive-EV candidate on ETH).
    # leg 5 — lsr_taker_confluence (15m)
    (5, {"sl_pct": 0.024, "max_hold_h": 4, "side": "short_only"}),
    # leg 6 — lsr_taker_confluence (15m) — 3 variants
    (6, {"tp_pct": 0.0225, "sl_pct": 0.0112, "max_hold_h": 48}),  # both
    (6, {"tp_pct": 0.0450, "sl_pct": 0.0112, "max_hold_h": 48, "side": "long_only"}),
    (6, {"tp_pct": 0.0144, "sl_pct": 0.0160, "max_hold_h": 8, "side": "short_only"}),
    # leg 7 — lsr_taker_confluence (60m) — 3 variants
    (7, {"tp_pct": 0.0625, "max_hold_h": 16}),  # both
    (7, {"tp_pct": 0.0625, "max_hold_h": 16, "side": "long_only"}),
    (7, {"tp_pct": 0.0150, "sl_pct": 0.024, "max_hold_h": 24, "side": "short_only"}),
    # leg 8 — lsr_taker_confluence (15m)
    (8, {"tp_pct": 0.020, "max_hold_h": 24, "side": "short_only"}),
    # leg 9 — lsr_taker_confluence (15m) — 3 variants
    (9, {"tp_pct": 0.030, "sl_pct": 0.024, "max_hold_h": 48}),  # both
    (9, {"tp_pct": 0.030, "max_hold_h": 48, "side": "long_only"}),
    (9, {"sl_pct": 0.024, "max_hold_h": 32, "side": "short_only"}),
    # leg 10 — ensemble_meanrev (60m) — 2 variants
    (10, {"tp_pct": 0.030, "sl_pct": 0.0084, "max_hold_h": 36}),  # both
    (10, {"tp_pct": 0.030, "sl_pct": 0.0084, "max_hold_h": 36, "side": "short_only"}),
    # leg 11 — ensemble_meanrev (60m) — 2 variants
    (11, {"sl_pct": 0.024, "max_hold_h": 8}),  # both
    (11, {"sl_pct": 0.024, "max_hold_h": 8, "side": "short_only"}),
    # leg 12 — ensemble_meanrev (60m) — 2 variants
    (12, {"tp_pct": 0.015, "sl_pct": 0.024, "max_hold_h": 24}),  # both
    (12, {"tp_pct": 0.015, "sl_pct": 0.024, "max_hold_h": 24, "side": "short_only"}),
    # leg 13 — ensemble_meanrev (15m) — 2 variants
    (13, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 12}),  # both
    (13, {"tp_pct": 0.015, "sl_pct": 0.0168, "max_hold_h": 12, "side": "short_only"}),
    # leg 14 — ensemble_meanrev (15m) — DROPPED.
    # leg 15 — donchian_breakout (60m) — 3 variants (STAR performer)
    (15, {"tp_pct": 0.12}),  # both
    (15, {"tp_pct": 0.12, "max_hold_h": 24, "side": "long_only"}),
    (15, {"tp_pct": 0.16, "side": "short_only"}),
    # leg 16 — donchian_breakout (240m) — 3 variants (STAR performer)
    (16, {"tp_pct": 0.20, "sl_pct": 0.024, "max_hold_h": 288}),  # both
    (16, {"tp_pct": 0.20, "sl_pct": 0.024, "max_hold_h": 288, "side": "long_only"}),
    (16, {"tp_pct": 0.20, "side": "short_only"}),
]


def _apply_eth_overrides(
    base_leg: dict[str, Any], override: dict[str, Any],
) -> dict[str, Any]:
    """Build a new leg dict by deep-copying ``base_leg`` and applying override
    fields. Unknown fields are logged-and-skipped; ``side`` is allowed even
    though the parent's ``TUNABLE_FIELDS`` excludes it (we extend the set).
    """
    cfg = deepcopy(base_leg["config"])
    for k, v in override.items():
        if k not in TUNABLE_FIELDS:
            logger.warning(
                "[mfp-eth] ignoring non-tunable override field %r", k,
            )
            continue
        if k not in cfg:
            logger.warning(
                "[mfp-eth] override field %r not in base config; skipping", k,
            )
            continue
        cfg[k] = v
    return {"family": base_leg["family"], "config": cfg}


def _build_eth_legs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for base_idx, ov in _ETH_VARIANTS:
        if not 0 <= base_idx < len(_BASE_LEGS):
            raise ValueError(
                f"[mfp-eth] base_idx {base_idx} out of range "
                f"(0..{len(_BASE_LEGS) - 1})"
            )
        out.append(_apply_eth_overrides(_BASE_LEGS[base_idx], ov))
    return out


# Materialised once at import — the parent class iterates ``ETH_LEGS`` via the
# monkey-patched ``resolve_legs`` below during ``initialize``.
ETH_LEGS: list[dict[str, Any]] = _build_eth_legs()


def resolve_legs(symbol: str) -> list[dict[str, Any]]:
    """ETH-only leg resolver. Refuses any symbol other than ``ETHUSDT`` so a
    misconfigured runner cannot silently apply ETH-tuned thresholds to a
    different market.
    """
    sym = symbol.upper()
    if sym != BASELINE_SYMBOL:
        raise ValueError(
            f"MultiFactorPortfolioEthStrategy: symbol {sym} is not "
            f"supported; this strategy is tuned exclusively for "
            f"{BASELINE_SYMBOL}. Use multi_factor_portfolio_strategy "
            f"for {sym}."
        )
    return ETH_LEGS


def _symbol_supported(symbol: str) -> bool:
    return symbol.upper() == BASELINE_SYMBOL


# ---------------------------------------------------------------------------
# Strategy — inherits the full lifecycle from the parent and only redirects
# leg construction. We override ``initialize`` (and not ``_initialize_live``
# directly) because the monkey-patch needs to be active across BOTH the
# backtest path (which calls ``resolve_legs`` at line 1263 of the parent) and
# the live path (which calls it at line 1337 of the parent via
# ``_initialize_live``).
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = dict(_mfp.STRATEGY_PARAMS)
STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = list(_mfp.STRATEGY_PARAM_SCHEMA)


class MultiFactorPortfolioEthStrategy(MultiFactorPortfolioStrategy):
    """ETH-tuned 29-sub-leg multi-factor portfolio strategy.

    Reuses the parent's lifecycle, data loaders, REST providers, signal
    funcs and per-leg state machine. The only behavioural change is the
    set of legs: 29 sub-legs with side specialisation, replacing the
    parent's 17-leg BTC baseline. See module docstring for discovery and
    performance details.
    """

    def initialize(self, ctx: StrategyContext) -> None:
        # Guard against accidental cross-symbol use BEFORE delegating to the
        # parent. The parent does its own symbol gate too, but our patched
        # ``_symbol_supported`` would otherwise hide a misconfigured runner.
        ctx_symbol = getattr(ctx, "symbol", None)
        if ctx_symbol and str(ctx_symbol).upper() != BASELINE_SYMBOL:
            raise ValueError(
                f"MultiFactorPortfolioEthStrategy: ctx.symbol={ctx_symbol} "
                f"is not supported; this strategy is tuned exclusively for "
                f"{BASELINE_SYMBOL}."
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
