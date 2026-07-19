from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from core.auth import (
    create_jwt,
    get_current_user,
    send_email_ses,
    send_magic_link,
    verify_jwt,
    verify_magic_link,
)

_EMAIL = "dev@katha.life"
_USER_ID = "test_user_wa"


def _make_db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


# ── JWT ──────────────────────────────────────────────────────────────────────


def test_create_and_verify_jwt_roundtrip():
    token = create_jwt(_EMAIL, _USER_ID)
    payload = verify_jwt(token)
    assert payload["sub"] == _EMAIL
    assert payload["user_id"] == _USER_ID


def test_verify_jwt_raises_401_for_expired_token():
    with patch("core.auth.settings") as mock_settings:
        mock_settings.JWT_SECRET = "test-secret"
        expired = jwt.encode(
            {
                "sub": _EMAIL,
                "user_id": _USER_ID,
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            "test-secret",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc_info:
            verify_jwt(expired)
    assert exc_info.value.status_code == 401


def test_verify_jwt_raises_401_for_tampered_token():
    token = create_jwt(_EMAIL, _USER_ID)
    tampered = token[:-4] + ("A" * 4)
    with pytest.raises(HTTPException) as exc_info:
        verify_jwt(tampered)
    assert exc_info.value.status_code == 401


def test_get_current_user_raises_401_without_cookie():
    request = MagicMock()
    request.cookies = {}
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(request)
    assert exc_info.value.status_code == 401


def test_get_current_user_returns_payload_with_valid_cookie():
    token = create_jwt(_EMAIL, _USER_ID)
    request = MagicMock()
    request.cookies = {"katha_token": token}
    payload = get_current_user(request)
    assert payload["sub"] == _EMAIL
    assert payload["user_id"] == _USER_ID


# ── magic link ───────────────────────────────────────────────────────────────


async def test_send_magic_link_noop_for_unknown_email():
    db = _make_db()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)

    with patch("core.auth.send_email_ses") as mock_send:
        await send_magic_link("unknown@example.com", db)

    mock_send.assert_not_called()
    db.add.assert_not_called()


async def test_send_magic_link_calls_ses_for_known_email():
    db = _make_db()
    account = MagicMock()
    account.email = _EMAIL
    result = MagicMock()
    result.scalar_one_or_none.return_value = account
    db.execute = AsyncMock(return_value=result)

    with patch("core.auth.send_email_ses") as mock_send:
        await send_magic_link(_EMAIL, db)

    mock_send.assert_called_once()
    call_args = mock_send.call_args.args
    assert call_args[0] == _EMAIL
    db.add.assert_called_once()


async def test_verify_magic_link_raises_400_for_expired_or_unknown_token():
    db = _make_db()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as exc_info:
        await verify_magic_link("bogus-token", db)
    assert exc_info.value.status_code == 400


async def test_verify_magic_link_marks_used_and_returns_email_user_id():
    db = _make_db()
    link = MagicMock()
    link.id = uuid.uuid4()
    link.email = _EMAIL

    account = MagicMock()
    account.email = _EMAIL
    account.user_id = _USER_ID

    link_result = MagicMock()
    link_result.scalar_one_or_none.return_value = link
    account_result = MagicMock()
    account_result.scalar_one_or_none.return_value = account

    db.execute = AsyncMock(side_effect=[link_result, MagicMock(), account_result])

    email, user_id = await verify_magic_link("real-token", db)

    assert email == _EMAIL
    assert user_id == _USER_ID
    db.commit.assert_called()


# ── SES helper ───────────────────────────────────────────────────────────────


def test_send_email_ses_mock_mode_does_not_call_boto3():
    with (
        patch("core.auth.settings") as mock_settings,
        patch("core.auth.boto3.client") as mock_boto_client,
    ):
        mock_settings.SES_MOCK = True
        send_email_ses(_EMAIL, "Subject", "text body", "<p>html body</p>")
    mock_boto_client.assert_not_called()


def test_send_email_ses_calls_boto3_when_not_mocked():
    mock_client = MagicMock()
    with (
        patch("core.auth.settings") as mock_settings,
        patch("core.auth.boto3.client", return_value=mock_client) as mock_boto_client,
    ):
        mock_settings.SES_MOCK = False
        mock_settings.SES_FROM_EMAIL = "noreply@katha.life"
        mock_settings.AWS_S3_REGION = "ap-south-1"
        send_email_ses(_EMAIL, "Subject", "text body", "<p>html body</p>")

    mock_boto_client.assert_called_once_with("ses", region_name="ap-south-1")
    mock_client.send_email.assert_called_once()
