from fastapi import HTTPException


class UpstreamError(HTTPException):
    """A third-party API (DeepSeek, ElevenLabs, Paystack, Daraja, Supabase) failed."""

    def __init__(self, service: str, detail: str = "upstream request failed"):
        super().__init__(status_code=502, detail=f"{service}: {detail}")


class NotConfigured(HTTPException):
    def __init__(self, service: str):
        super().__init__(status_code=503, detail=f"{service} is not configured on the server")
