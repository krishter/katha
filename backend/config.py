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

    # WhatsApp / Twilio
    WHATSAPP_ADAPTER: str = "twilio"  # "stub" in dev/test
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_NUMBER: str = "whatsapp:+917019058242"
    # The production sender sits in a Messaging Service; when set, sends go
    # through it rather than the bare number. Empty keeps the sandbox path.
    TWILIO_MESSAGING_SERVICE_SID: str = ""
    WEBHOOK_VERIFY_TOKEN: str = "katha-webhook-verify"

    # WhatsApp message template SIDs (filled after Meta approval).
    # Empty defaults on purpose: a SID is environment-specific, and baking
    # one in means a machine with no configuration still sends against a
    # real approved template. Set them in .env — see .env.example.
    TWILIO_TEMPLATE_SESSION_OPEN: str = ""
    TWILIO_TEMPLATE_FOLLOWUP: str = ""
    TWILIO_TEMPLATE_MEMORY_CARD: str = ""
    TWILIO_TEMPLATE_PARENT_WELCOME: str = ""
    TWILIO_TEMPLATE_PARENT_WELCOME_FOLLOWUP: str = ""

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

    # The domain the session cookie is scoped to. Empty in development,
    # where it must stay empty: the browser then makes the cookie
    # host-only, and localhost:3000 and localhost:8000 share one jar
    # because cookies ignore port numbers.
    #
    # In production the frontend and the API are genuinely different
    # hostnames — app.katha.life and api.katha.life. The magic-link verify
    # sets the cookie on api., so a host-only cookie is invisible to app.,
    # and every /family/* route redirects to /family/login forever, for
    # everyone. Setting ".katha.life" scopes it to both.
    #
    # Set it through core.auth.set_session_cookie / clear_session_cookie
    # rather than reading it directly: a delete whose domain does not match
    # the set silently leaves a live session cookie behind, which after
    # DELETE /user/{user_id} is a DPDP problem, not a cosmetic one.
    COOKIE_DOMAIN: str = ""


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
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
    ):
        if not getattr(s, name):
            problems.append(f"{name} is empty")
    if not s.APP_BASE_URL.startswith("https://"):
        problems.append(f"APP_BASE_URL is not https:// (got: {s.APP_BASE_URL!r})")
    if not s.COOKIE_DOMAIN:
        problems.append(
            "COOKIE_DOMAIN is empty — the session cookie would be host-only, "
            "so a cookie set on api. is invisible to app. and every family "
            "is locked out of the dashboard permanently"
        )
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
