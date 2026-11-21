"""Plans and checkout: Paystack for cards, Daraja STK push for M-Pesa."""

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.db import Supabase, eq, get_db
from app.http import get_http
from app.phone import normalise_kenyan_phone
from app.plans import PLANS, get_plan, price_kes, price_usd
from app.services import billing, daraja, paystack

router = APIRouter(prefix="/billing", tags=["billing"])


class Checkout(BaseModel):
    plan_id: Literal["plus", "pro"]
    period: Literal["monthly", "yearly"]


class MpesaCheckout(Checkout):
    phone: str


@router.get("/plans")
def plans(settings: Settings = Depends(get_settings)) -> list[dict]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "daily_minutes": p.daily_minutes,
            "can_record": p.can_record,
            "prices": {
                period: {"usd": price_usd(p, period), "kes": price_kes(p, period, settings.usd_to_kes)}
                for period in ("monthly", "yearly")
            },
        }
        for p in PLANS.values()
    ]


@router.get("/me")
async def my_subscription(user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)) -> dict:
    sub = await db.select_one("subscriptions", {"user_id": eq(user.id)})
    plan_id = await billing.current_plan_id(db, user.id)
    return {
        "plan_id": plan_id,
        "period": sub.get("period") if sub else None,
        "current_period_end": sub.get("current_period_end") if sub else None,
    }


# --- Paystack (cards) ---------------------------------------------------------------------------


@router.post("/paystack/initialize")
async def paystack_initialize(
    body: Checkout,
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not user.email:
        raise HTTPException(400, "Add an email to your account to pay by card")
    plan = get_plan(body.plan_id)
    amount = price_kes(plan, body.period, settings.usd_to_kes)
    reference = f"nimo_{uuid.uuid4().hex}"
    await db.insert(
        "payments",
        {
            "user_id": user.id,
            "provider": "paystack",
            "plan_id": plan.id,
            "period": body.period,
            "amount_minor": amount * 100,
            "currency": settings.paystack_currency,
            "reference": reference,
            "status": "pending",
        },
    )
    data = await paystack.initialize(
        http,
        settings,
        email=user.email,
        amount_minor=amount * 100,
        reference=reference,
        metadata={"user_id": user.id, "plan_id": plan.id, "period": body.period},
    )
    return {"authorization_url": data["authorization_url"], "reference": reference}


@router.get("/paystack/verify/{reference}")
async def paystack_verify(
    reference: str,
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Called by the app after the Paystack page closes. The webhook does the same thing as a backup."""
    payment = await db.select_one("payments", {"reference": eq(reference), "user_id": eq(user.id)})
    if payment is None:
        raise HTTPException(404, "Payment not found")
    if payment["status"] == "paid":
        return {"status": "paid", "plan_id": payment["plan_id"]}
    data = await paystack.verify(http, settings, reference)
    if data["status"] == "success" and int(data["amount"]) >= payment["amount_minor"]:
        payment = await billing.mark_payment(db, {"id": eq(payment["id"])}, "paid", receipt=str(data.get("id")))
        await billing.activate_from_payment(db, payment)
        await billing.save_card(db, user.id, (data.get("customer") or {}).get("email"), data.get("authorization"))
        return {"status": "paid", "plan_id": payment["plan_id"]}
    if data["status"] in {"failed", "abandoned", "reversed"}:
        await billing.mark_payment(db, {"id": eq(payment["id"])}, "failed")
        return {"status": "failed"}
    return {"status": "pending"}


# --- M-Pesa (Daraja STK push) -------------------------------------------------------------------


@router.post("/mpesa/stk-push")
async def mpesa_stk_push(
    body: MpesaCheckout,
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
) -> dict:
    phone = normalise_kenyan_phone(body.phone)
    if phone is None:
        raise HTTPException(422, "Enter a valid Kenyan mobile number")
    if not settings.daraja_callback_token:
        raise HTTPException(503, "DARAJA_CALLBACK_TOKEN is not configured")
    plan = get_plan(body.plan_id)
    amount = price_kes(plan, body.period, settings.usd_to_kes)
    result = await daraja.stk_push(
        http,
        settings,
        phone=phone,
        amount=amount,
        account_reference=f"Nimo{plan.name}",
        description=f"Nimo {plan.name}",
        callback_url=f"{settings.callback_base_url}/webhooks/mpesa/{settings.daraja_callback_token}",
    )
    await db.insert(
        "payments",
        {
            "user_id": user.id,
            "provider": "mpesa",
            "plan_id": plan.id,
            "period": body.period,
            "amount_minor": amount * 100,
            "currency": "KES",
            "reference": result["MerchantRequestID"],
            "checkout_request_id": result["CheckoutRequestID"],
            "phone": phone,
            "status": "pending",
        },
    )
    if not await db.select_one("payment_methods", {"user_id": eq(user.id), "kind": eq("mpesa"), "phone": eq(phone)}):
        has_default = await db.select_one("payment_methods", {"user_id": eq(user.id), "is_default": "is.true"})
        await db.insert(
            "payment_methods",
            {"user_id": user.id, "kind": "mpesa", "phone": phone, "is_default": has_default is None},
        )
    return {
        "checkout_request_id": result["CheckoutRequestID"],
        "amount_kes": amount,
        "message": result.get("CustomerMessage"),
    }


@router.get("/mpesa/status/{checkout_request_id}")
async def mpesa_status(
    checkout_request_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
) -> dict:
    """The app polls this every few seconds while the PIN prompt is open."""
    payment = await db.select_one(
        "payments", {"checkout_request_id": eq(checkout_request_id), "user_id": eq(user.id)}
    )
    if payment is None:
        raise HTTPException(404, "Payment not found")
    if payment["status"] != "pending":
        return {"status": payment["status"], "plan_id": payment["plan_id"]}
    # Callback not in yet: ask Safaricom directly.
    query = await daraja.stk_query(http, settings, checkout_request_id)
    code = str(query.get("ResultCode", ""))
    if code == "0":
        payment = await billing.mark_payment(db, {"id": eq(payment["id"])}, "paid")
        await billing.activate_from_payment(db, payment)
        return {"status": "paid", "plan_id": payment["plan_id"]}
    if code and code not in {"4999"}:  # 4999 = still processing
        await billing.mark_payment(db, {"id": eq(payment["id"])}, "failed", failure_reason=query.get("ResultDesc"))
        return {"status": "failed", "reason": query.get("ResultDesc")}
    return {"status": "pending"}


class SavedCardCheckout(Checkout):
    method_id: str


@router.post("/paystack/charge-saved")
async def paystack_charge_saved(
    body: SavedCardCheckout,
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Pays with a card saved from an earlier Paystack payment. May need 2FA (`authorization_url`)."""
    method = await db.select_one(
        "payment_methods", {"id": eq(body.method_id), "user_id": eq(user.id), "kind": eq("card")}
    )
    if method is None or not method.get("authorization_code"):
        raise HTTPException(404, "Card not found")
    plan = get_plan(body.plan_id)
    amount = price_kes(plan, body.period, settings.usd_to_kes)
    reference = f"nimo_{uuid.uuid4().hex}"
    [payment] = await db.insert(
        "payments",
        {
            "user_id": user.id,
            "provider": "paystack",
            "plan_id": plan.id,
            "period": body.period,
            "amount_minor": amount * 100,
            "currency": settings.paystack_currency,
            "reference": reference,
            "status": "pending",
        },
    )
    data = await paystack.charge_authorization(
        http,
        settings,
        email=method.get("email") or user.email or "",
        amount_minor=amount * 100,
        authorization_code=method["authorization_code"],
        reference=reference,
        metadata={"user_id": user.id, "plan_id": plan.id, "period": body.period},
    )
    if data.get("paused") and data.get("authorization_url"):
        return {"status": "action_required", "authorization_url": data["authorization_url"], "reference": reference}
    if data.get("status") == "success":
        payment = await billing.mark_payment(db, {"id": eq(payment["id"])}, "paid", receipt=str(data.get("id")))
        await billing.activate_from_payment(db, payment)
        return {"status": "paid", "plan_id": plan.id, "reference": reference}
    await billing.mark_payment(db, {"id": eq(payment["id"])}, "failed", failure_reason=data.get("gateway_response"))
    return {"status": "failed", "reason": data.get("gateway_response") or "The card was declined"}


# --- Saved payment methods ----------------------------------------------------------------------


class NewMpesaMethod(BaseModel):
    phone: str


def _public_method(m: dict) -> dict:
    if m["kind"] == "mpesa":
        return {"id": m["id"], "kind": "mpesa", "phone": m["phone"], "is_default": m["is_default"]}
    month, year = (m.get("exp_month") or "").zfill(2), (m.get("exp_year") or "")[-2:]
    return {
        "id": m["id"],
        "kind": "card",
        "brand": m.get("brand") or "Card",
        "last4": m.get("last4") or "",
        "expiry": f"{month}/{year}" if year else "",
        "is_default": m["is_default"],
    }


@router.get("/payment-methods")
async def list_payment_methods(user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)) -> list:
    rows = await db.select("payment_methods", {"user_id": eq(user.id)}, order="created_at.asc")
    return [_public_method(m) for m in rows]


@router.post("/payment-methods/mpesa")
async def add_mpesa_method(
    body: NewMpesaMethod, user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)
) -> dict:
    phone = normalise_kenyan_phone(body.phone)
    if phone is None:
        raise HTTPException(422, "Enter a valid Kenyan mobile number")
    existing = await db.select_one("payment_methods", {"user_id": eq(user.id), "kind": eq("mpesa"), "phone": eq(phone)})
    if existing:
        return _public_method(existing)
    has_default = await db.select_one("payment_methods", {"user_id": eq(user.id), "is_default": "is.true"})
    [row] = await db.insert(
        "payment_methods", {"user_id": user.id, "kind": "mpesa", "phone": phone, "is_default": has_default is None}
    )
    return _public_method(row)


@router.post("/payment-methods/{method_id}/default")
async def make_default(
    method_id: str, user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)
) -> dict:
    if not await db.select_one("payment_methods", {"id": eq(method_id), "user_id": eq(user.id)}):
        raise HTTPException(404, "Payment method not found")
    await db.update("payment_methods", {"user_id": eq(user.id)}, {"is_default": False})
    await db.update("payment_methods", {"id": eq(method_id)}, {"is_default": True})
    return {"ok": True}


@router.delete("/payment-methods/{method_id}")
async def remove_payment_method(
    method_id: str, user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)
) -> dict:
    method = await db.select_one("payment_methods", {"id": eq(method_id), "user_id": eq(user.id)})
    if method is None:
        raise HTTPException(404, "Payment method not found")
    await db.delete("payment_methods", {"id": eq(method_id)})
    if method["is_default"]:
        nxt = await db.select_one("payment_methods", {"user_id": eq(user.id)})
        if nxt:
            await db.update("payment_methods", {"id": eq(nxt["id"])}, {"is_default": True})
    return {"ok": True}


# --- Usage ---------------------------------------------------------------------------------------


@router.get("/usage")
async def usage(user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)) -> dict:
    """Minutes per day for the last 7 days (UTC days), this month's totals and today's allowance."""
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=6)
    month_start = today.replace(day=1)
    rows = await db.select(
        "calls",
        {"user_id": eq(user.id), "started_at": f"gte.{min(since, month_start).isoformat()}"},
        columns="started_at,duration_seconds",
    )
    month = [r for r in rows if datetime.fromisoformat(r["started_at"]).date() >= month_start]
    per_day: dict[date, int] = {since + timedelta(days=i): 0 for i in range(7)}
    for r in rows:
        day = datetime.fromisoformat(r["started_at"]).date()
        if day in per_day:
            per_day[day] += r.get("duration_seconds") or 0
    plan = get_plan(await billing.current_plan_id(db, user.id))
    return {
        "plan_id": plan.id,
        "daily_minutes": plan.daily_minutes,
        "today_minutes": round(per_day[today] / 60, 1),
        "days": [{"date": d.isoformat(), "minutes": round(sec / 60, 1)} for d, sec in per_day.items()],
        "month_minutes": round(sum(r.get("duration_seconds") or 0 for r in month) / 60),
        "month_calls": sum(1 for r in month if (r.get("duration_seconds") or 0) > 0),
    }
