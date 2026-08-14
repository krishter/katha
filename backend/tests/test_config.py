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
