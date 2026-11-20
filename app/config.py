import os
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "staging", "production"] = "development"
    cors_origins: str = "http://localhost:8081"
    #: Public HTTPS URL used in payment callbacks. On Render this falls back to RENDER_EXTERNAL_URL.
    public_base_url: str = ""

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    elevenlabs_api_key: str = ""
    elevenlabs_tts_model: str = "eleven_flash_v2_5"
    elevenlabs_stt_model: str = "scribe_v1"
    elevenlabs_output_format: str = "mp3_44100_128"
    elevenlabs_fallback_voice_id: str = "L0Dsvb3SLTyegXwtm47J"

    paystack_secret_key: str = ""
    paystack_currency: str = "KES"
    paystack_callback_url: str = "nimo://payment-complete"

    daraja_env: Literal["sandbox", "production"] = "sandbox"
    daraja_consumer_key: str = ""
    daraja_consumer_secret: str = ""
    daraja_shortcode: str = "174379"
    daraja_passkey: str = ""
    daraja_transaction_type: str = "CustomerPayBillOnline"
    daraja_callback_token: str = ""

    usd_to_kes: float = 129

    @property
    def callback_base_url(self) -> str:
        url = self.public_base_url or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000"
        return url.rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def daraja_base_url(self) -> str:
        return "https://api.safaricom.co.ke" if self.daraja_env == "production" else "https://sandbox.safaricom.co.ke"


@lru_cache
def get_settings() -> Settings:
    return Settings()
