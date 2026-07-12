from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    DATABASE_URL: str = "postgresql+asyncpg://katha:katha@localhost:5432/katha"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "info"

    SARVAM_API_KEY: str
    ANTHROPIC_API_KEY: str
    OPENAI_API_KEY: str

    # WhatsApp / Twilio
    WHATSAPP_ADAPTER: str = "twilio"  # "stub" in dev/test
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_NUMBER: str = "whatsapp:+14155238886"
    WEBHOOK_VERIFY_TOKEN: str = "katha-webhook-verify"

    # WhatsApp message template SIDs (filled after Meta approval)
    TWILIO_TEMPLATE_SESSION_OPEN: str = ""
    TWILIO_TEMPLATE_FOLLOWUP: str = ""
    TWILIO_TEMPLATE_MEMORY_CARD: str = ""

    # AWS S3 (Mumbai — DPDP Act data residency)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_S3_BUCKET: str = "katha-media"
    AWS_S3_REGION: str = "ap-south-1"


settings = Settings()
