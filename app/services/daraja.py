"""Safaricom Daraja: M-Pesa Express (STK push) and status query."""

import base64
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.config import Settings
from app.errors import NotConfigured, UpstreamError

# Kenya is UTC+3 all year (no daylight saving), so a fixed offset avoids needing tzdata on Windows.
NAIROBI = timezone(timedelta(hours=3), "EAT")

_token: dict[str, float | str] = {"value": "", "expires": 0.0}


def timestamp(now: datetime | None = None) -> str:
    """Daraja expects YYYYMMDDHHmmss in Kenyan time."""
    return (now or datetime.now(NAIROBI)).strftime("%Y%m%d%H%M%S")


def password(shortcode: str, passkey: str, ts: str) -> str:
    return base64.b64encode(f"{shortcode}{passkey}{ts}".encode()).decode()


async def access_token(http: httpx.AsyncClient, settings: Settings) -> str:
    if not (settings.daraja_consumer_key and settings.daraja_consumer_secret and settings.daraja_passkey):
        raise NotConfigured("Daraja")
    if _token["value"] and time.time() < float(_token["expires"]):
        return str(_token["value"])
    res = await http.get(
        f"{settings.daraja_base_url}/oauth/v1/generate",
        params={"grant_type": "client_credentials"},
        auth=(settings.daraja_consumer_key, settings.daraja_consumer_secret),
    )
    if res.status_code >= 400:
        raise UpstreamError("Daraja", res.text[:300])
    body = res.json()
    _token["value"] = body["access_token"]
    # Refresh a minute early.
    _token["expires"] = time.time() + int(body.get("expires_in", 3599)) - 60
    return str(_token["value"])


async def stk_push(
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    phone: str,
    amount: int,
    account_reference: str,
    description: str,
    callback_url: str,
) -> dict:
    """Sends the PIN prompt to the phone. Returns MerchantRequestID, CheckoutRequestID, ResponseCode, CustomerMessage."""
    token = await access_token(http, settings)
    ts = timestamp()
    res = await http.post(
        f"{settings.daraja_base_url}/mpesa/stkpush/v1/processrequest",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "BusinessShortCode": settings.daraja_shortcode,
            "Password": password(settings.daraja_shortcode, settings.daraja_passkey, ts),
            "Timestamp": ts,
            "TransactionType": settings.daraja_transaction_type,
            "Amount": amount,
            "PartyA": phone,
            "PartyB": settings.daraja_shortcode,
            "PhoneNumber": phone,
            "CallBackURL": callback_url,
            "AccountReference": account_reference[:12],
            "TransactionDesc": description[:13],
        },
    )
    body = res.json() if res.content else {}
    if res.status_code >= 400 or body.get("ResponseCode") != "0":
        raise UpstreamError("Daraja", body.get("errorMessage") or body.get("ResponseDescription") or res.text[:300])
    return body


async def stk_query(http: httpx.AsyncClient, settings: Settings, checkout_request_id: str) -> dict:
    token = await access_token(http, settings)
    ts = timestamp()
    res = await http.post(
        f"{settings.daraja_base_url}/mpesa/stkpushquery/v1/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "BusinessShortCode": settings.daraja_shortcode,
            "Password": password(settings.daraja_shortcode, settings.daraja_passkey, ts),
            "Timestamp": ts,
            "CheckoutRequestID": checkout_request_id,
        },
    )
    if res.status_code >= 400:
        raise UpstreamError("Daraja", res.text[:300])
    return res.json()


def parse_callback(body: dict) -> dict:
    """Flattens Daraja's STK callback into {checkout_request_id, result_code, result_desc, receipt, amount, phone}."""
    cb = body["Body"]["stkCallback"]
    items = {i["Name"]: i.get("Value") for i in cb.get("CallbackMetadata", {}).get("Item", [])}
    return {
        "checkout_request_id": cb["CheckoutRequestID"],
        "result_code": int(cb["ResultCode"]),
        "result_desc": cb.get("ResultDesc", ""),
        "receipt": items.get("MpesaReceiptNumber"),
        "amount": items.get("Amount"),
        "phone": str(items["PhoneNumber"]) if items.get("PhoneNumber") else None,
    }
