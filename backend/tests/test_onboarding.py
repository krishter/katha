from __future__ import annotations

import hashlib
from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.auth import get_current_user
from main import app
from models.db import get_db

client = TestClient(app)

_USER_ID = "user_abc123"
_EMAIL = "dev@katha.life"


def _fake_current_user():
    return {"sub": _EMAIL, "user_id": _USER_ID}


@pytest.fixture
def db():
    """
    Override get_db/get_current_user for one test, then restore whatever was
    there before — other test modules register their own module-level
    get_db override on the same shared `app`, and a bare pop() would delete
    that override for the rest of the pytest session instead of restoring it.
    """
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    async def _override_get_db():
        yield mock_db

    prev_db_override = app.dependency_overrides.get(get_db)
    prev_user_override = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield mock_db
    finally:
        if prev_db_override is not None:
            app.dependency_overrides[get_db] = prev_db_override
        else:
            app.dependency_overrides.pop(get_db, None)
        if prev_user_override is not None:
            app.dependency_overrides[get_current_user] = prev_user_override
        else:
            app.dependency_overrides.pop(get_current_user, None)


def _query_result(**kwargs):
    result = MagicMock()
    for key, value in kwargs.items():
        getattr(result, key).return_value = value
    return result


# ── /onboarding/start ────────────────────────────────────────────────────────


def test_onboarding_start_new_email_creates_account_and_sends_link(db):
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=None))

    with patch(
        "api.routes.onboarding.auth.send_magic_link", new=AsyncMock()
    ) as mock_send:
        response = client.post("/onboarding/start", data={"email": "new@katha.life"})

    assert response.status_code == 200
    assert response.json()["status"] == "new"
    db.add.assert_called_once()
    mock_send.assert_called_once()


def test_onboarding_start_existing_complete_account_returns_existing(db):
    account = MagicMock()
    account.onboarding_complete = True
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=account))

    with patch(
        "api.routes.onboarding.auth.send_magic_link", new=AsyncMock()
    ) as mock_send:
        response = client.post(
            "/onboarding/start", data={"email": "existing@katha.life"}
        )

    assert response.status_code == 200
    assert response.json()["status"] == "existing"
    mock_send.assert_called_once()


def test_onboarding_start_existing_incomplete_account_still_sends_a_link(db):
    """A family that registered their parent and stopped before the consent
    step has to be able to get back in. This case used to return without
    sending anything while both callers told the visitor to check their
    email, which locked the account out permanently."""
    account = MagicMock()
    account.onboarding_complete = False
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=account))

    with patch(
        "api.routes.onboarding.auth.send_magic_link", new=AsyncMock(return_value=True)
    ) as mock_send:
        response = client.post("/onboarding/start", data={"email": "mid@katha.life"})

    assert response.status_code == 200
    assert response.json()["status"] == "incomplete"
    assert response.json()["link_sent"] is True
    mock_send.assert_called_once()
    # Still must not create a second account for an address that has one.
    db.add.assert_not_called()


def test_onboarding_start_reports_a_failed_send(db):
    """The visitor must not be told to check their email when the mail
    transport refused the message."""
    account = MagicMock()
    account.onboarding_complete = True
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=account))

    with patch(
        "api.routes.onboarding.auth.send_magic_link", new=AsyncMock(return_value=False)
    ):
        response = client.post("/onboarding/start", data={"email": "x@katha.life"})

    assert response.status_code == 200
    assert response.json()["link_sent"] is False


# ── /onboarding/profile ──────────────────────────────────────────────────────

_VALID_PROFILE_PAYLOAD = {
    "parent_name": "Subramaniam",
    "whatsapp_number": "+919876543210",
    "family_whatsapp_number": "+919876543211",
    "preferred_language": "ta-IN",
    "session_time": "09:30",
    "onboarding_context": "Grew up in Madurai.",
}


def test_onboarding_profile_invalid_phone_returns_422(db):
    payload = {**_VALID_PROFILE_PAYLOAD, "whatsapp_number": "98765-43210"}
    response = client.post("/onboarding/profile", data=payload)
    assert response.status_code == 422


def test_onboarding_profile_invalid_session_time_returns_422(db):
    payload = {**_VALID_PROFILE_PAYLOAD, "session_time": "9:30am"}
    response = client.post("/onboarding/profile", data=payload)
    assert response.status_code == 422


def test_onboarding_profile_valid_payload_upserts_profile(db):
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=None))

    response = client.post("/onboarding/profile", data=_VALID_PROFILE_PAYLOAD)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    db.add.assert_called_once()
    saved_profile = db.add.call_args.args[0]
    assert saved_profile.name == "Subramaniam"
    assert saved_profile.family_whatsapp_number == "+919876543211"
    # An older client that sends no persona keeps the voice parents know.
    assert saved_profile.katha_gender == "female"


def test_onboarding_profile_saves_the_chosen_persona(db):
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=None))
    payload = {**_VALID_PROFILE_PAYLOAD, "katha_gender": "male"}

    response = client.post("/onboarding/profile", data=payload)

    assert response.status_code == 200
    assert db.add.call_args.args[0].katha_gender == "male"


def test_onboarding_profile_rejects_an_unknown_persona(db):
    payload = {**_VALID_PROFILE_PAYLOAD, "katha_gender": "robot"}
    response = client.post("/onboarding/profile", data=payload)
    assert response.status_code == 422


# ── /onboarding/consent ──────────────────────────────────────────────────────


def test_onboarding_consent_false_returns_400(db):
    response = client.post("/onboarding/consent", data={"consent_given": "false"})
    assert response.status_code == 400


def test_onboarding_consent_true_creates_record_and_completes_onboarding(db):
    account = MagicMock()
    profile = MagicMock()
    profile.name = "Subramaniam"
    profile.scheduled_time = time(9, 30)

    db.execute = AsyncMock(
        side_effect=[
            _query_result(scalar_one_or_none=account),
            _query_result(scalar_one_or_none=profile),
        ]
    )

    response = client.post(
        "/onboarding/consent",
        data={"consent_given": "true"},
        headers={"User-Agent": "pytest"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete"
    assert body["parent_name"] == "Subramaniam"
    assert body["session_time"] == "09:30"
    assert account.onboarding_complete is True


def test_onboarding_consent_stores_email_hash_not_raw_email(db):
    account = MagicMock()
    profile = MagicMock()
    profile.name = "Subramaniam"
    profile.scheduled_time = time(9, 30)

    db.execute = AsyncMock(
        side_effect=[
            _query_result(scalar_one_or_none=account),
            _query_result(scalar_one_or_none=profile),
        ]
    )

    client.post("/onboarding/consent", data={"consent_given": "true"})

    db.add.assert_called_once()
    saved_record = db.add.call_args.args[0]
    expected_hash = hashlib.sha256(_EMAIL.encode()).hexdigest()
    assert saved_record.email_hash == expected_hash
    assert saved_record.user_id == _USER_ID
    assert _EMAIL not in saved_record.email_hash


def test_consent_version_is_current():
    """S2.3 bumped the checklist text: it now links to a deletion control
    that exists (F-01) and states that Katha asks the parent directly
    rather than implying the buyer can consent for them (F-02). Records
    written against the old wording keep saying 1.0 — this column exists to
    say which text was agreed to, so changing the text without changing the
    version would make every historical record a claim about copy the user
    never saw."""
    from api.routes.onboarding import _CONSENT_VERSION

    assert _CONSENT_VERSION == "1.1"


# ── the pinned voice (docs/specs/GEMINI_TTS.md §4.2) ─────────────────────────


@pytest.fixture
def gemini_default(monkeypatch):
    monkeypatch.setattr("config.settings.TTS_VENDOR", "gemini")
    monkeypatch.setattr("config.settings.GEMINI_TTS_LANGUAGES", "all")


def test_a_new_profile_is_pinned_to_the_default_voice(db, gemini_default):
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=None))
    payload = {**_VALID_PROFILE_PAYLOAD, "katha_gender": "male"}

    client.post("/onboarding/profile", data=payload)

    saved = db.add.call_args.args[0]
    assert saved.tts_voice == "google/gemini-3.8-flash-lite-tts/Achird"


def test_a_new_urdu_profile_is_pinned_to_sarvam(db, gemini_default):
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=None))
    payload = {**_VALID_PROFILE_PAYLOAD, "preferred_language": "ur-IN"}

    client.post("/onboarding/profile", data=payload)

    assert db.add.call_args.args[0].tts_voice.startswith("sarvam/")


def _existing(language, gender, pin):
    from models.user_profile import UserProfileModel

    return UserProfileModel(
        user_id=_USER_ID,
        preferred_language=language,
        katha_gender=gender,
        tts_voice=pin,
    )


def test_resending_setup_unchanged_keeps_the_pin(db, gemini_default):
    """A family editing the context must not change the voice she hears."""
    pinned = "sarvam/bulbul:v4-flash/ritu_hi_conversation"
    existing = _existing(_VALID_PROFILE_PAYLOAD["preferred_language"], "female", pinned)
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=existing))

    payload = {**_VALID_PROFILE_PAYLOAD, "onboarding_context": "Loves cricket."}
    client.post("/onboarding/profile", data=payload)

    assert existing.tts_voice == pinned


def test_changing_language_or_persona_at_setup_moves_the_pin(db, gemini_default):
    pinned = "sarvam/bulbul:v4-flash/ritu_hi_conversation"
    existing = _existing(_VALID_PROFILE_PAYLOAD["preferred_language"], "female", pinned)
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=existing))

    payload = {**_VALID_PROFILE_PAYLOAD, "katha_gender": "male"}
    client.post("/onboarding/profile", data=payload)

    assert existing.tts_voice == "google/gemini-3.8-flash-lite-tts/Achird"
