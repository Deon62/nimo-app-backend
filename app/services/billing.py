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


async def save_card(db: Supabase, user_id: str, email: str | None, authorization: dict | None) -> None:
    """Stores a reusable Paystack card so it can be charged again. Duplicate cards are ignored."""
    if not authorization or not authorization.get("reusable") or not authorization.get("authorization_code"):
        return
    signature = authorization.get("signature")
    if signature and await db.select_one(
        "payment_methods", {"user_id": eq(user_id), "kind": eq("card"), "signature": eq(signature)}
    ):
        return
    has_default = await db.select_one("payment_methods", {"user_id": eq(user_id), "is_default": "is.true"})
    brand = (authorization.get("brand") or authorization.get("card_type") or "card").strip().title()
    await db.insert(
        "payment_methods",
        {
            "user_id": user_id,
            "kind": "card",
            "brand": brand,
            "last4": authorization.get("last4"),
            "exp_month": authorization.get("exp_month"),
            "exp_year": authorization.get("exp_year"),
            "email": email,
            "authorization_code": authorization["authorization_code"],
            "signature": signature,
            "is_default": has_default is None,
        },
    )
