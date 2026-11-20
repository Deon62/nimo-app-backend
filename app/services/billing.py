"""Payment records and plan activation. Activation is idempotent: a payment activates once."""

from datetime import datetime, timedelta, timezone

from app.db import Supabase, eq
from app.plans import Period


def _extend(start: datetime, period: Period) -> datetime:
    return start + (timedelta(days=365) if period == "yearly" else timedelta(days=30))


async def activate_from_payment(db: Supabase, payment: dict) -> None:
    """Marks the plan active for the payment's user. Safe to call more than once."""
    if payment.get("activated_at"):
        return
    now = datetime.now(timezone.utc)
    current = await db.select_one("subscriptions", {"user_id": eq(payment["user_id"])})
    start = now
    if current and current.get("plan_id") == payment["plan_id"] and current.get("current_period_end"):
        # Renewing the same plan early stacks on top of the remaining time.
        start = max(now, datetime.fromisoformat(current["current_period_end"]))
    await db.upsert(
        "subscriptions",
        {
            "user_id": payment["user_id"],
            "plan_id": payment["plan_id"],
            "period": payment["period"],
            "status": "active",
            "current_period_end": _extend(start, payment["period"]).isoformat(),
            "updated_at": now.isoformat(),
        },
        on_conflict="user_id",
    )
    await db.update("payments", {"id": eq(payment["id"])}, {"activated_at": now.isoformat()})


async def mark_payment(db: Supabase, filters: dict[str, str], status: str, **extra) -> dict | None:
    rows = await db.update(
        "payments",
        filters,
        {"status": status, "updated_at": datetime.now(timezone.utc).isoformat(), **extra},
    )
    return rows[0] if rows else None


async def current_plan_id(db: Supabase, user_id: str) -> str:
    sub = await db.select_one("subscriptions", {"user_id": eq(user_id)})
    if not sub or sub.get("status") != "active" or not sub.get("current_period_end"):
        return "free"
    if datetime.fromisoformat(sub["current_period_end"]) < datetime.now(timezone.utc):
        return "free"
    return sub["plan_id"]
