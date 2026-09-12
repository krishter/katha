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
