"""Supabase Auth hooks. Supabase calls these, not the app."""

import base64
import hashlib
import hmac
import re
import time

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.http import get_http
from app.services import celcom

router = APIRouter(prefix="/hooks", tags=["hooks"])

#: Reject signed requests older or newer than this (replay protection).
TOLERANCE_SECONDS = 5 * 60


def signature_is_valid(secret: str, msg_id: str, timestamp: str, body: bytes, header: str) -> bool:
    """Standard Webhooks check: HMAC-SHA256 of "id.timestamp.body" with the base64 secret."""
    if not (secret and msg_id and timestamp and header):
        return False
    try:
        if abs(time.time() - int(timestamp)) > TOLERANCE_SECONDS:
            return False
        key = base64.b64decode(secret.removeprefix("v1,").removeprefix("whsec_"))
    except ValueError:
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    # The header can hold several space-separated "v1,<signature>" values.
    return any(
        hmac.compare_digest(part.split(",", 1)[1], expected)
        for part in header.split()
        if part.startswith("v1,") and "," in part
    )


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": {"http_code": status, "message": message}}, status_code=status)


@router.post("/send-sms")
async def send_sms_hook(
    request: Request,
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
):
    """Sends Supabase's phone sign-in code through Celcom instead of Twilio."""
    body = await request.body()
    if not signature_is_valid(
        settings.send_sms_hook_secret,
        request.headers.get("webhook-id", ""),
        request.headers.get("webhook-timestamp", ""),
        body,
        request.headers.get("webhook-signature", ""),
    ):
        return _error(401, "Invalid signature")

    payload = await request.json()
    phone = re.sub(r"\D", "", str((payload.get("user") or {}).get("phone", "")))
    otp = str((payload.get("sms") or {}).get("otp", ""))
    if not phone or not otp:
        return _error(422, "Missing phone or code")
    try:
        await celcom.send_sms(http, settings, phone, f"Your Nimo code is {otp}. Don't share it with anyone.")
    except Exception as e:  # noqa: BLE001 - Supabase needs a JSON error, whatever went wrong
        return _error(500, f"Could not send SMS: {e}")
    return JSONResponse({})
