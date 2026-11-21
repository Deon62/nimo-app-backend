"""Server-side source of truth for prices. Never trust an amount sent by the app."""

import math
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException

PlanId = Literal["free", "plus", "pro"]
Period = Literal["monthly", "yearly"]

YEARLY_DISCOUNT = 0.2


@dataclass(frozen=True)
class Plan:
    id: PlanId
    name: str
    monthly_usd: float
    #: None means unlimited.
    daily_minutes: int | None
    can_record: bool


PLANS: dict[str, Plan] = {
    "free": Plan("free", "Free", 0, 10, False),
    "plus": Plan("plus", "Plus", 9.99, 60, True),
    "pro": Plan("pro", "Pro", 24.99, None, True),
}


#: Higher is better; you can't move down while a paid plan is still running.
RANK: dict[str, int] = {"free": 0, "plus": 1, "pro": 2}


def get_plan(plan_id: str) -> Plan:
    plan = PLANS.get(plan_id)
    if plan is None:
        raise HTTPException(404, "Plan not found")
    return plan


def price_usd(plan: Plan, period: Period) -> float:
    if period == "monthly":
        return plan.monthly_usd
    return round(plan.monthly_usd * 12 * (1 - YEARLY_DISCOUNT), 2)


def price_kes(plan: Plan, period: Period, usd_to_kes: float) -> int:
    """Whole shillings (M-Pesa only accepts whole numbers)."""
    return math.ceil(price_usd(plan, period) * usd_to_kes)
