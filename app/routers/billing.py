"""Plans and checkout: Paystack for cards, Daraja STK push for M-Pesa."""

import uuid
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
        callback_url=f"{settings.public_base_url.rstrip('/')}/webhooks/mpesa/{settings.daraja_callback_token}",
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
