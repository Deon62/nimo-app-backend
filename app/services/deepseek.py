"""DeepSeek chat completions (OpenAI-compatible)."""

import httpx

from app.config import Settings
from app.errors import NotConfigured, UpstreamError

# Voice replies should be short; long answers sound unnatural on a call.
MAX_REPLY_TOKENS = 300


async def chat(http: httpx.AsyncClient, settings: Settings, messages: list[dict], temperature: float = 0.8) -> str:
    if not settings.deepseek_api_key:
        raise NotConfigured("DeepSeek")
    res = await http.post(
        f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
        json={
            "model": settings.deepseek_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": MAX_REPLY_TOKENS,
            "stream": False,
        },
        timeout=60,
    )
    if res.status_code >= 400:
        raise UpstreamError("DeepSeek", res.text[:300])
    return res.json()["choices"][0]["message"]["content"].strip()
