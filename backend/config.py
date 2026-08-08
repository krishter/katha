from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_JWT_SECRET = "dev-only-insecure-secret-change-me"


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

    # Family dashboard auth (Phase 6)
    JWT_SECRET: str = _DEFAULT_JWT_SECRET
    JWT_EXPIRE_DAYS: int = 7
    MAGIC_LINK_EXPIRE_MINUTES: int = 15
    SES_FROM_EMAIL: str = "noreply@katha.life"
    APP_BASE_URL: str = "http://localhost:3000"
    SES_MOCK: bool = True  # print the magic link instead of sending via SES

    # This backend's own externally-reachable base URL — used to reconstruct
    # the exact URL Twilio signed, since request.url reports the scheme the
    # TLS-terminating load balancer used internally (http), not the public
    # one Twilio actually POSTed to (https). Never derived from request
    # headers (X-Forwarded-Proto is spoofable without a trusted-host list).
    PUBLIC_BASE_URL: str = "http://localhost:8000"


settings = Settings()


def validate_production_config(s: Settings = settings) -> None:
    """
    Refuse to boot in production with an unsafe configuration. A backend
    running with the default JWT secret, a stub WhatsApp adapter, or a
    mocked email sender is worse than a backend that is down (C8) — the
    failure would be silent and total (forgeable session cookies, magic
    links that never send, no real conversations).
    """
    if s.ENVIRONMENT != "production":
        return

    problems: list[str] = []

    if s.JWT_SECRET == _DEFAULT_JWT_SECRET or len(s.JWT_SECRET) < 32:
        problems.append("JWT_SECRET is the default value or shorter than 32 characters")
    if s.SES_MOCK:
        problems.append("SES_MOCK is True — magic link emails would never send")
    if s.WHATSAPP_ADAPTER == "stub":
        problems.append("WHATSAPP_ADAPTER is 'stub' — no real messages would send")
    for name in (
        "ANTHROPIC_API_KEY",
        "SARVAM_API_KEY",
        "OPENAI_API_KEY",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
    ):
        if not getattr(s, name):
            problems.append(f"{name} is empty")
    if not s.APP_BASE_URL.startswith("https://"):
        problems.append(f"APP_BASE_URL is not https:// (got: {s.APP_BASE_URL!r})")
    if not s.PUBLIC_BASE_URL.startswith("https://"):
        problems.append(
            f"PUBLIC_BASE_URL is not https:// (got: {s.PUBLIC_BASE_URL!r}) — "
            "every Twilio webhook signature check would fail"
        )

    if problems:
        raise RuntimeError(
            "Refusing to start in production with unsafe configuration:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
