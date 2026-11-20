"""Server-to-server notifications from Paystack and Safaricom. Always answer 200 quickly once handled."""

import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import Settings, get_settings
from app.db import Supabase, eq, get_db
from app.services import billing, daraja, paystack

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger("nimo.webhooks")


@router.post("/paystack")
async def paystack_webhook(
    request: Request, db: Supabase = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict:
    raw = await request.body()
    if not paystack.signature_is_valid(raw, request.headers.get("x-paystack-signature"), settings.paystack_secret_key):
        raise HTTPException(401, "Invalid signature")
    event = json.loads(raw)
    if event.get("event") != "charge.success":
        return {"ok": True}
    data = event["data"]
    payment = await db.select_one("payments", {"reference": eq(data["reference"])})
    if payment is None or payment["status"] == "paid":
        return {"ok": True}
    if int(data["amount"]) < payment["amount_minor"]:
        log.warning("Paystack amount mismatch for %s", data["reference"])
        return {"ok": True}
    payment = await billing.mark_payment(db, {"id": eq(payment["id"])}, "paid", receipt=str(data.get("id")))
    await billing.activate_from_payment(db, payment)
    return {"ok": True}


@router.post("/mpesa/{token}")
async def mpesa_webhook(
    token: str, request: Request, db: Supabase = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict:
    # Daraja callbacks aren't signed, so the URL carries a secret token.
    if not settings.daraja_callback_token or not hmac.compare_digest(token, settings.daraja_callback_token):
        raise HTTPException(404)
    result = daraja.parse_callback(await request.json())
    payment = await db.select_one("payments", {"checkout_request_id": eq(result["checkout_request_id"])})
    if payment is None or payment["status"] != "pending":
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    if result["result_code"] == 0:
        if result["amount"] is not None and int(result["amount"]) * 100 < payment["amount_minor"]:
            log.warning("M-Pesa amount mismatch for %s", result["checkout_request_id"])
            return {"ResultCode": 0, "ResultDesc": "Accepted"}
        payment = await billing.mark_payment(db, {"id": eq(payment["id"])}, "paid", receipt=result["receipt"])
        await billing.activate_from_payment(db, payment)
    else:
        await billing.mark_payment(db, {"id": eq(payment["id"])}, "failed", failure_reason=result["result_desc"])
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
