from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanLimits:
    max_live_jobs: int
    max_backtest_per_month: int
    max_llm_generate_per_month: int
    portfolio_mode: bool
    priority_queue: bool


PLANS: dict[str, PlanLimits] = {
    "free": PlanLimits(
        max_live_jobs=0,
        max_backtest_per_month=10,
        max_llm_generate_per_month=5,
        portfolio_mode=False,
        priority_queue=False,
    ),
    "pro": PlanLimits(
        max_live_jobs=1,
        max_backtest_per_month=100,
        max_llm_generate_per_month=50,
        portfolio_mode=False,
        priority_queue=True,
    ),
    "enterprise": PlanLimits(
        max_live_jobs=10,
        max_backtest_per_month=9999,
        max_llm_generate_per_month=9999,
        portfolio_mode=True,
        priority_queue=True,
    ),
}


def get_plan_limits(plan: str) -> PlanLimits:
    return PLANS.get(plan, PLANS["free"])
