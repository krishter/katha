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
    # Gemini TTS (docs/specs/GEMINI_TTS.md). Must be a key from a
    # billing-enabled project: on the free tier Google may use the content
    # to improve its products. Required in production while TTS_VENDOR is
    # gemini.
    GEMINI_API_KEY: str = ""

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
    # The memory-card template is not optional in production. The adult
    # child never messages Katha's number, so no 24-hour customer-service
    # window is ever open for them, and a free-form media message is
    # accepted by Twilio and then rejected by Meta with 63016 — silently,
    # since the rejection arrives out of band.
    #
    # The registered template must use this variable mapping, which
    # orchestrator._generate_and_deliver_memory_card relies on:
    #   {{1}}  media — the card image URL (injected by the adapter)
    #   {{2}}  the parent's name
    #   {{3}}  the life domain the memory came from
    TWILIO_TEMPLATE_MEMORY_CARD: str = ""
    # The host baked into that template's media URL.
    #
    # Twilio only allows a variable in a template's media URL after a fixed
    # domain, so the approved template holds
    # "https://<this host>/{{1}}" and we pass only the key and query
    # string as {{1}}. That makes the host a contract between the template
    # registered at Meta and the bucket we presign against: if they ever
    # diverge, every card 404s at Meta's fetcher with nothing in our logs,
    # so the adapter refuses to send rather than guess.
    #
    # Derive it, do not type it from memory: it is
    # <AWS_S3_BUCKET>.s3.<AWS_S3_REGION>.amazonaws.com, which follows from
    # the virtual addressing media/storage.py pins deliberately. For the
    # current production bucket that is
    # katha-media-500923064555-ap-south-1-an.s3.ap-south-1.amazonaws.com
    # — note the bucket is NOT plain "katha-media"; deploy_bkp.txt says it
    # is and is wrong, which is exactly the mistake this setting exists to
    # turn into a loud failure instead of a silent one.
    TWILIO_TEMPLATE_MEDIA_HOST: str = ""
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

    # Cost to serve (docs/specs/COST_TO_SERVE.md). Prices live in
    # core/pricing.py; these are only what the ledger needs to report.
    #
    # USD to INR, for reporting Anthropic and Twilio costs in rupees. Not
    # used to charge anyone.
    USD_INR_RATE: float = 95.06
    # Meta category of each approved template, as a JSON object keyed by
    # template SID: {"HX...": "utility", "HX...": "marketing"}. Meta, not
    # us, decides a template's category, and it charges per category, so
    # the adapter cannot infer it. A template missing from this map is
    # recorded as "unknown" and shows up as unpriced on the admin page
    # rather than silently reading as free.
    META_TEMPLATE_CATEGORIES: str = ""

    # C2: build Layer 3 once per session, on its first turn, and reuse it
    # for every later turn. Keeps the start of the dialogue prompt identical
    # within a session, which is what lets prompt caching work. false
    # rebuilds it every turn, as before. The rendering caps apply either way.
    LAYER3_FREEZE_PER_SESSION: bool = True

    # Which Claude model each call runs on (COST_TO_SERVE.md C1, C4). Every
    # one rolls back with an env change and a restart: the old path stays in
    # the code until the pilot has run a month on the new one.
    #   Dialogue — rollback value claude-sonnet-4-6.
    LLM_DIALOGUE_MODEL: str = "claude-sonnet-5-5"
    # output_config.effort for dialogue. low: the eval passed as well as
    # medium at lower cost (evals/results/20261016T170550Z.json against
    # 20261016T170401Z.json), which is the spec's rule for choosing.
    LLM_DIALOGUE_EFFORT: str = "low"
    #   Per-turn extraction, its retry, and entity extraction — rollback
    #   value claude-sonnet-4-6.
    LLM_EXTRACTION_MODEL: str = "claude-haiku-5-5"
    LLM_EXTRACTION_EFFORT: str = "low"

    # C3: cache the stable start of the dialogue system prompt (Layers 1-3).
    PROMPT_CACHE_ENABLED: bool = True
    # "5m" or "1h". Parents take minutes between voice notes, so the
    # five-minute cache would usually expire between turns.
    PROMPT_CACHE_TTL: str = "1h"

    # C5: the parent starts every conversation (decided 2026-10-16, Option B
    # in docs/proposals/outbound-messaging-window.md). Off, the scheduler
    # only sweeps stale sessions and produces outputs for abandoned ones; a
    # session opens when she sends a voice note. true restores the daily
    # voice opener and the 30-minute nudge, which Meta rejects outside the
    # 24-hour window — a rollback, not a mode to run in.
    SCHEDULED_OPENER_ENABLED: bool = False

    # C6: the Sarvam TTS model. v4 Flash since 2026-10-17: preferred in 19 of
    # 20 blind pairs (COST_TO_SERVE.md §10). The parent's language and Katha's
    # persona then pick the model and voice per media/voices.py, which keeps
    # a language on v3 where v4 has no conversation voice for the persona.
    # bulbul:v3 is the rollback: every persona on v3.
    TTS_MODEL: str = "bulbul:v4-flash"

    # Which vendor a NEW family's voice comes from (GEMINI_TTS.md §4.2).
    # gemini since 2026-10-18; sarvam is the rollback for new families.
    # Existing parents keep the voice pinned on their profile either way;
    # scripts/set_voice.py moves them.
    TTS_VENDOR: str = "gemini"
    # The languages Gemini speaks for Katha, comma-separated, or "all" for
    # all twelve it supports (every Katha language but Urdu). Removing a
    # language sends new families in it back to Sarvam.
    GEMINI_TTS_LANGUAGES: str = "all"

    # C7: shorten long pauses before STT, which bills by the second sent.
    # Every pause longer than STT_MAX_PAUSE_SECONDS is cut to that length
    # (see media/audio_split.py for why it is one number); speech is never
    # cut. false sends the note exactly as received.
    STT_TRIM_PAUSES: bool = True
    STT_MAX_PAUSE_SECONDS: float = 0.5

    # Operator metrics page (docs/specs/ADMIN_METRICS.md). Empty token means
    # /admin/metrics and /admin/metrics/view answer 404, as if they did not
    # exist. Generate with:
    #   python -c "import secrets; print(secrets.token_urlsafe(32))"
    ADMIN_API_TOKEN: str = ""
    # Comma-separated user_ids left out of every metric: self-trial accounts
    # and rehearsal users. The page reports how many, never which.
    METRICS_EXCLUDED_USER_IDS: str = ""

    # Conversation cards (docs/specs/CONVERSATION_CARDS.md). Off: nothing
    # about a session changes, the per-session memory card included. On:
    # cards run only for parents whose profile has
    # conversation_cards_enabled, which scripts/cards.py turns on one
    # family at a time.
    CONVERSATION_CARDS_ENABLED: bool = False
    # D3: every card is reviewed by the operator before she hears it.
    # false sends a card that passed the fidelity check straight to her.
    CARDS_OPERATOR_REVIEW: bool = True
    # Where "a card is waiting for review" and "she said never share"
    # alerts go. Empty: logged only.
    OPERATOR_EMAIL: str = ""
    # katha_conversation_card_v2 (§8.1). Variable mapping:
    #   {{1}}  media — the card image (injected by the adapter, via
    #          TWILIO_TEMPLATE_MEDIA_HOST, as for the memory card)
    #   {{2}}  parent_title, what the child calls her ("Amma")
    #   {{3}}  the card's call prompt
    # Empty: cards go by email only. Never sent free-form.
    TWILIO_TEMPLATE_CONVERSATION_CARD: str = ""
    # Card selection, writing and the fidelity check (§7). The fidelity
    # check is what keeps words out of her mouth, so it runs on the larger
    # model.
    LLM_CARD_MODEL: str = "claude-sonnet-5-5"
    # output_config.effort for those calls. At the model's default a
    # Hindi card used its whole token budget thinking and returned no text
    # (TC-17, 2026-10-19).
    LLM_CARD_EFFORT: str = "medium"
    # Her answers to an ask, and screening the child's questions (§9.3,
    # §10.2).
    LLM_CLASSIFIER_MODEL: str = "claude-haiku-5-5"


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
    if s.ADMIN_API_TOKEN and len(s.ADMIN_API_TOKEN) < 32:
        problems.append(
            "ADMIN_API_TOKEN is set but shorter than 32 characters — it is the "
            "only thing guarding the operator metrics page"
        )
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
    if s.TTS_VENDOR not in ("gemini", "sarvam"):
        problems.append(f"TTS_VENDOR must be gemini or sarvam (got: {s.TTS_VENDOR!r})")
    elif s.TTS_VENDOR == "gemini" and not s.GEMINI_API_KEY:
        problems.append(
            "TTS_VENDOR is gemini but GEMINI_API_KEY is empty — every new "
            "family's voice note would fall back to Sarvam"
        )
    if not s.APP_BASE_URL.startswith("https://"):
        problems.append(f"APP_BASE_URL is not https:// (got: {s.APP_BASE_URL!r})")
    if not s.COOKIE_DOMAIN:
        problems.append(
            "COOKIE_DOMAIN is empty — the session cookie would be host-only, "
            "so a cookie set on api. is invisible to app. and every family "
            "is locked out of the dashboard permanently"
        )
    if s.TWILIO_TEMPLATE_MEMORY_CARD and not s.TWILIO_TEMPLATE_MEDIA_HOST:
        problems.append(
            "TWILIO_TEMPLATE_MEMORY_CARD is set but TWILIO_TEMPLATE_MEDIA_HOST "
            "is empty — the card's media URL is assembled by the approved "
            "template from that host, so without it no card can be sent"
        )
    if s.TWILIO_TEMPLATE_CONVERSATION_CARD and not s.TWILIO_TEMPLATE_MEDIA_HOST:
        problems.append(
            "TWILIO_TEMPLATE_CONVERSATION_CARD is set but "
            "TWILIO_TEMPLATE_MEDIA_HOST is empty — the card image's URL is "
            "assembled by the template from that host"
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
