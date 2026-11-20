"""ElevenLabs text-to-speech and speech-to-text."""

import httpx

from app.config import Settings
from app.errors import NotConfigured, UpstreamError

BASE = "https://api.elevenlabs.io/v1"


def _headers(settings: Settings) -> dict[str, str]:
    if not settings.elevenlabs_api_key:
        raise NotConfigured("ElevenLabs")
    return {"xi-api-key": settings.elevenlabs_api_key}


async def text_to_speech(http: httpx.AsyncClient, settings: Settings, voice_id: str, text: str) -> bytes:
    """Returns encoded audio (MP3 by default)."""
    res = await http.post(
        f"{BASE}/text-to-speech/{voice_id}",
        params={"output_format": settings.elevenlabs_output_format},
        headers={**_headers(settings), "Accept": "audio/mpeg"},
        json={
            "text": text,
            "model_id": settings.elevenlabs_tts_model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=60,
    )
    if res.status_code >= 400:
        raise UpstreamError("ElevenLabs", res.text[:300])
    return res.content


async def speech_to_text(
    http: httpx.AsyncClient, settings: Settings, audio: bytes, filename: str, content_type: str
) -> str:
    res = await http.post(
        f"{BASE}/speech-to-text",
        headers=_headers(settings),
        data={"model_id": settings.elevenlabs_stt_model},
        files={"file": (filename, audio, content_type)},
        timeout=60,
    )
    if res.status_code >= 400:
        raise UpstreamError("ElevenLabs", res.text[:300])
    return (res.json().get("text") or "").strip()
