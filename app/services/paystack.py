"""Paystack card payments: initialize a transaction, verify it, and check webhook signatures."""

import hashlib
import hmac

import httpx

from app.config import Settings
from app.errors import NotConfigured, UpstreamError

BASE = "https://api.paystack.co"


def _headers(settings: Settings) -> dict[str, str]:
    if not settings.paystack_secret_key:
        raise NotConfigured("Paystack")
    return {"Authorization": f"Bearer {settings.paystack_secret_key}"}


async def initialize(
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    email: str,
    amount_minor: int,
    reference: str,
    metadata: dict,
) -> dict:
    """Returns {authorization_url, access_code, reference}. Open authorization_url in a browser/webview."""
    res = await http.post(
        f"{BASE}/transaction/initialize",
        headers=_headers(settings),
        json={
            "email": email,
            "amount": amount_minor,
            "currency": settings.paystack_currency,
            "reference": reference,
            "callback_url": settings.paystack_callback_url,
            "channels": ["card"],
            "metadata": metadata,
        },
    )
    body = res.json() if res.content else {}
    if res.status_code >= 400 or not body.get("status"):
        raise UpstreamError("Paystack", body.get("message", res.text[:300]))
    return body["data"]


async def verify(http: httpx.AsyncClient, settings: Settings, reference: str) -> dict:
    res = await http.get(f"{BASE}/transaction/verify/{reference}", headers=_headers(settings))
    body = res.json() if res.content else {}
    if res.status_code >= 400 or not body.get("status"):
        raise UpstreamError("Paystack", body.get("message", res.text[:300]))
    return body["data"]


def signature_is_valid(raw_body: bytes, signature: str | None, secret_key: str) -> bool:
    """Paystack signs the raw webhook body with HMAC-SHA512 using your secret key."""
    if not signature or not secret_key:
        return False
    expected = hmac.new(secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


async def charge_authorization(
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    email: str,
    amount_minor: int,
    authorization_code: str,
    reference: str,
    metadata: dict,
) -> dict:
    """Charges a saved card. Returns Paystack's `data`; if `paused` is true, open `authorization_url` for 2FA."""
    res = await http.post(
        f"{BASE}/transaction/charge_authorization",
        headers=_headers(settings),
        json={
            "email": email,
            "amount": amount_minor,
            "currency": settings.paystack_currency,
            "authorization_code": authorization_code,
            "reference": reference,
            "metadata": metadata,
        },
    )
    body = res.json() if res.content else {}
    if res.status_code >= 400 or not body.get("status"):
        raise UpstreamError("Paystack", body.get("message", res.text[:300]))
    return body["data"]
