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


def test_onboarding_start_existing_incomplete_account_returns_incomplete(db):
    account = MagicMock()
    account.onboarding_complete = False
    db.execute = AsyncMock(return_value=_query_result(scalar_one_or_none=account))

    response = client.post("/onboarding/start", data={"email": "mid@katha.life"})

    assert response.status_code == 200
    assert response.json()["status"] == "incomplete"
    db.add.assert_not_called()


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
