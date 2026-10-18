import pathlib
from types import SimpleNamespace

import pytest

from config import validate_production_config

_SAFE_KWARGS = dict(
    ENVIRONMENT="production",
    JWT_SECRET="a" * 40,
    SES_MOCK=False,
    WHATSAPP_ADAPTER="twilio",
    ANTHROPIC_API_KEY="sk-ant-real",
    SARVAM_API_KEY="sk-sarvam-real",
    TWILIO_ACCOUNT_SID="ACreal",
    TWILIO_AUTH_TOKEN="realtoken",
    APP_BASE_URL="https://katha.life",
    PUBLIC_BASE_URL="https://api.katha.life",
    COOKIE_DOMAIN=".katha.life",
    # A deployment with no approved card template is still safe to boot —
    # the free-form path works in sandbox — so the safe baseline leaves
    # both unset and the pairing is asserted separately below.
    TWILIO_TEMPLATE_MEMORY_CARD="",
    TWILIO_TEMPLATE_MEDIA_HOST="",
    # Unset means the operator metrics routes 404 — safe.
    ADMIN_API_TOKEN="",
    TTS_VENDOR="gemini",
    GEMINI_API_KEY="AIza-real",
)


def _settings(**overrides) -> SimpleNamespace:
    kwargs = {**_SAFE_KWARGS, **overrides}
    return SimpleNamespace(**kwargs)


def test_safe_production_config_does_not_raise():
    validate_production_config(_settings())


def test_development_config_is_never_validated():
    """Any of these would be unsafe in production, but development must be
    unaffected regardless of what's set."""
    unsafe = _settings(
        ENVIRONMENT="development",
        JWT_SECRET="dev-only-insecure-secret-change-me",
        SES_MOCK=True,
        WHATSAPP_ADAPTER="stub",
        ANTHROPIC_API_KEY="",
        APP_BASE_URL="http://localhost:3000",
    )
    validate_production_config(unsafe)


def test_default_jwt_secret_raises_in_production():
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        validate_production_config(
            _settings(JWT_SECRET="dev-only-insecure-secret-change-me")
        )


def test_short_jwt_secret_raises_in_production():
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        validate_production_config(_settings(JWT_SECRET="short"))


def test_ses_mock_true_raises_in_production():
    with pytest.raises(RuntimeError, match="SES_MOCK"):
        validate_production_config(_settings(SES_MOCK=True))


def test_stub_whatsapp_adapter_raises_in_production():
    with pytest.raises(RuntimeError, match="WHATSAPP_ADAPTER"):
        validate_production_config(_settings(WHATSAPP_ADAPTER="stub"))


@pytest.mark.parametrize(
    "key",
    [
        "ANTHROPIC_API_KEY",
        "SARVAM_API_KEY",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
    ],
)
def test_empty_credential_raises_in_production(key):
    with pytest.raises(RuntimeError, match=key):
        validate_production_config(_settings(**{key: ""}))


def test_non_https_base_url_raises_in_production():
    with pytest.raises(RuntimeError, match="APP_BASE_URL"):
        validate_production_config(_settings(APP_BASE_URL="http://katha.life"))


def test_non_https_public_base_url_raises_in_production():
    """Every Twilio webhook signature check derives from PUBLIC_BASE_URL —
    left at its http:// dev default in production, every webhook 403s."""
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        validate_production_config(_settings(PUBLIC_BASE_URL="http://localhost:8000"))


def test_reports_every_problem_at_once():
    with pytest.raises(RuntimeError) as exc_info:
        validate_production_config(
            _settings(JWT_SECRET="short", SES_MOCK=True, WHATSAPP_ADAPTER="stub")
        )
    message = str(exc_info.value)
    assert "JWT_SECRET" in message
    assert "SES_MOCK" in message
    assert "WHATSAPP_ADAPTER" in message


def test_empty_cookie_domain_raises_in_production():
    """
    A host-only cookie set on api.katha.life is invisible to
    app.katha.life, so every family is redirected to the login page
    forever. The failure is silent and total, which is exactly what this
    boot check exists to catch (DEPLOYMENT.md Step 0.1).
    """
    with pytest.raises(RuntimeError, match="COOKIE_DOMAIN"):
        validate_production_config(_settings(COOKIE_DOMAIN=""))


def test_env_example_documents_every_production_critical_setting():
    """
    `.env.example` is what someone copies when standing up a new
    environment, so a setting missing from it is inherited at its
    development default — silently. That is precisely how PUBLIC_BASE_URL
    went missing (DEPLOYMENT.md Step 0.2): the production boot check caught
    it, but only because somebody had thought to add that check.

    Every name validate_production_config inspects must therefore appear in
    the example file.
    """
    example = (
        pathlib.Path(__file__).resolve().parent.parent.parent / ".env.example"
    ).read_text()
    documented = {
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }

    missing = sorted(name for name in _SAFE_KWARGS if name not in documented)
    assert not missing, (
        f".env.example is missing production-critical settings: {missing}"
    )


def test_a_card_template_without_its_media_host_raises_in_production():
    """The template builds its media URL as https://<host>/{{1}}. With the
    host unset the adapter cannot assemble or check that URL, so every
    card would fail at Meta's fetcher with nothing in our logs."""
    with pytest.raises(RuntimeError, match="TWILIO_TEMPLATE_MEDIA_HOST"):
        validate_production_config(_settings(TWILIO_TEMPLATE_MEMORY_CARD="HXabc"))


def test_a_card_template_with_its_media_host_is_fine():
    validate_production_config(
        _settings(
            TWILIO_TEMPLATE_MEMORY_CARD="HXabc",
            TWILIO_TEMPLATE_MEDIA_HOST="katha-media.s3.ap-south-1.amazonaws.com",
        )
    )


def test_a_short_admin_token_raises_in_production():
    """It is the only thing guarding the operator metrics page."""
    with pytest.raises(RuntimeError, match="ADMIN_API_TOKEN"):
        validate_production_config(_settings(ADMIN_API_TOKEN="too-short"))


def test_a_long_admin_token_is_fine():
    validate_production_config(_settings(ADMIN_API_TOKEN="x" * 32))


# ── TTS vendor (docs/specs/GEMINI_TTS.md §4.2) ──────────────────────────────


def test_gemini_is_the_default_vendor():
    from config import Settings

    assert Settings.model_fields["TTS_VENDOR"].default == "gemini"
    assert Settings.model_fields["GEMINI_TTS_LANGUAGES"].default == "all"


def test_gemini_without_a_key_refuses_to_boot():
    """Otherwise every new family's reply would quietly fall back to Sarvam."""
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        validate_production_config(_settings(GEMINI_API_KEY=""))


def test_sarvam_needs_no_gemini_key():
    validate_production_config(_settings(TTS_VENDOR="sarvam", GEMINI_API_KEY=""))


def test_an_unknown_vendor_refuses_to_boot():
    with pytest.raises(RuntimeError, match="TTS_VENDOR"):
        validate_production_config(_settings(TTS_VENDOR="svara"))
