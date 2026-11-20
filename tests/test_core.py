import base64
import hashlib
import hmac
import json
from datetime import datetime

from fastapi.testclient import TestClient

from app.agents import all_agents
from app.config import get_settings
from app.main import app
from app.phone import normalise_kenyan_phone
from app.plans import get_plan, price_kes, price_usd
from app.services import daraja, paystack


def test_agents_loaded_with_voices():
    agents = all_agents()
    assert len(agents) == 10
    assert agents["maya"].voice_id == "g6xIsTj2HwM6VR4iXFCw"
    # Ryan has no voice yet and falls back to the default.
    assert agents["ryan"].voice_id is None
    assert agents["ryan"].effective_voice_id == get_settings().elevenlabs_fallback_voice_id


def test_sample_lines_alternate_speakers():
    lines = all_agents()["alex"].sample_lines()
    assert lines and lines[0][0] == "user" and lines[1][0] == "agent"


def test_prices():
    plus = get_plan("plus")
    assert price_usd(plus, "monthly") == 9.99
    assert price_usd(plus, "yearly") == 95.9
    assert price_kes(plus, "monthly", 129) == 1289


def test_phone_normalisation():
    for raw in ["0712345678", "712345678", "+254712345678", "254 712 345 678", "0112345678"]:
        assert normalise_kenyan_phone(raw) is not None, raw
    assert normalise_kenyan_phone("0712345678") == "254712345678"
    assert normalise_kenyan_phone("0812345678") is None
    assert normalise_kenyan_phone("12345") is None


def test_daraja_password_and_timestamp():
    ts = daraja.timestamp(datetime(2026, 11, 21, 9, 5, 3))
    assert ts == "20261121090503"
    assert base64.b64decode(daraja.password("174379", "key", ts)).decode() == "174379key20261121090503"


def test_daraja_callback_parsing():
    body = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "m1",
                "CheckoutRequestID": "ws_CO_1",
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 1289},
                        {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
                        {"Name": "PhoneNumber", "Value": 254712345678},
                    ]
                },
            }
        }
    }
    parsed = daraja.parse_callback(body)
    assert parsed == {
        "checkout_request_id": "ws_CO_1",
        "result_code": 0,
        "result_desc": "The service request is processed successfully.",
        "receipt": "NLJ7RT61SV",
        "amount": 1289,
        "phone": "254712345678",
    }


def test_paystack_signature():
    raw = json.dumps({"event": "charge.success"}).encode()
    sig = hmac.new(b"sk_test_x", raw, hashlib.sha512).hexdigest()
    assert paystack.signature_is_valid(raw, sig, "sk_test_x")
    assert not paystack.signature_is_valid(raw, sig, "sk_test_other")
    assert not paystack.signature_is_valid(raw, None, "sk_test_x")


def test_http_endpoints():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        agents = client.get("/agents").json()
        assert len(agents) == 10 and "instructions" not in agents[0]
        plans = client.get("/billing/plans").json()
        assert plans[1]["prices"]["monthly"]["kes"] == 1289
        # Protected routes need a Supabase token.
        assert client.post("/calls", json={"agent_id": "alex"}).status_code == 401
        for method, path in [
            ("get", "/calls"),
            ("get", "/calls/x/recording"),
            ("delete", "/calls/x"),
            ("get", "/billing/usage"),
            ("get", "/billing/payment-methods"),
            ("post", "/billing/paystack/charge-saved"),
            ("delete", "/me"),
        ]:
            assert getattr(client, method)(path).status_code in (401, 422), path
        # Unsigned Paystack webhooks are rejected before touching the database.
        get_settings().supabase_url = get_settings().supabase_url or "https://example.supabase.co"
        get_settings().supabase_service_role_key = get_settings().supabase_service_role_key or "test"
        assert client.post("/webhooks/paystack", content=b"{}").status_code == 401
