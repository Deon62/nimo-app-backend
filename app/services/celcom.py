"""Celcom Africa SMS (https://celcomafrica.com/developers)."""

import httpx

from app.config import Settings
from app.errors import NotConfigured, UpstreamError

SEND_URL = "https://isms.celcomafrica.com/api/services/sendsms/"


async def send_sms(http: httpx.AsyncClient, settings: Settings, mobile: str, message: str) -> str:
    """Sends one SMS. `mobile` is digits only, e.g. 254712345678. Returns Celcom's message id."""
    if not (settings.celcom_api_key and settings.celcom_partner_id and settings.celcom_shortcode):
        raise NotConfigured("Celcom SMS")
    try:
        res = await http.post(
            SEND_URL,
            json={
                "apikey": settings.celcom_api_key,
                "partnerID": settings.celcom_partner_id,
                "shortcode": settings.celcom_shortcode,
                "mobile": mobile,
                "message": message,
                "pass_type": "plain",
            },
            # Supabase gives the hook 5 seconds in total.
            timeout=4.0,
        )
    except httpx.HTTPError as e:
        raise UpstreamError("Celcom", str(e)) from e
    body = res.json() if res.content else {}
    first = (body.get("responses") or [{}])[0]
    # The docs spell it "respose-code"; accept either spelling.
    code = first.get("respose-code", first.get("response-code"))
    if res.status_code >= 400 or str(code) != "200":
        raise UpstreamError("Celcom", str(first.get("response-description") or res.text[:200]))
    return str(first.get("messageid", ""))
