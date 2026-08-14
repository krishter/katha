from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.auth import get_current_user
from main import app
from models.db import get_db

client = TestClient(app)

_USER_ID = "user_abc123"
_OTHER_USER_ID = "user_xyz789"


def _fake_current_user():
    return {"sub": "dev@katha.life", "user_id": _USER_ID}


@pytest.fixture
def db():
    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def _override_get_db():
        yield mock_db

    prev_db = app.dependency_overrides.get(get_db)
    prev_user = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield mock_db
    finally:
        if prev_db is not None:
            app.dependency_overrides[get_db] = prev_db
        else:
            app.dependency_overrides.pop(get_db, None)
        if prev_user is not None:
            app.dependency_overrides[get_current_user] = prev_user
        else:
            app.dependency_overrides.pop(get_current_user, None)


def _default_execute_results(
    s3_keys=None, audio_keys=None, session_open_keys=None, email="dev@katha.life"
):
    """
    Build the ordered list of db.execute() return values matching
    admin.delete_user's call sequence:
    1. select memory_cards.image_s3_key
    2. select turns.response_audio_s3_key
    3. select sessions.session_open_audio_s3_key
    4-9. delete memory_cards / story_atoms / turns / facts / sessions /
         user_profiles
    10. select family_accounts.email
    11. delete magic_link_tokens (only if email found)
    12. update consent_records
    13. delete family_accounts
    """
    s3_key_result = MagicMock()
    s3_key_result.scalars.return_value.all.return_value = s3_keys or []

    audio_key_result = MagicMock()
    audio_key_result.scalars.return_value.all.return_value = audio_keys or []

    session_open_result = MagicMock()
    session_open_result.scalars.return_value.all.return_value = session_open_keys or []

    email_result = MagicMock()
    email_result.scalar_one_or_none.return_value = email

    generic = lambda: MagicMock()  # noqa: E731 — result of a DELETE/UPDATE, unused

    results = [s3_key_result, audio_key_result, session_open_result]
    results += [generic() for _ in range(6)]  # the 6 DELETE-by-user_id statements
    results.append(email_result)
    if email is not None:
        results.append(generic())  # delete magic_link_tokens
    results.append(generic())  # update consent_records
    results.append(generic())  # delete family_accounts
    return results


# ── auth isolation ────────────────────────────────────────────────────────────


def test_delete_returns_403_if_user_id_does_not_match_jwt(db):
    response = client.request("DELETE", f"/user/{_OTHER_USER_ID}")
    assert response.status_code == 403


# ── deletion behavior ────────────────────────────────────────────────────────


def test_delete_calls_s3_delete_for_each_memory_card(db):
    db.execute = AsyncMock(
        side_effect=_default_execute_results(s3_keys=["cards/a.png", "cards/b.png"])
    )

    with patch("api.routes.admin.storage.delete_media", new=AsyncMock()) as mock_delete:
        response = client.request("DELETE", f"/user/{_USER_ID}")

    assert response.status_code == 200
    assert mock_delete.await_count == 2
    called_keys = {c.args[0] for c in mock_delete.await_args_list}
    assert called_keys == {"cards/a.png", "cards/b.png"}


def test_delete_removes_rows_for_each_table(db):
    db.execute = AsyncMock(side_effect=_default_execute_results())

    with patch("api.routes.admin.storage.delete_media", new=AsyncMock()):
        response = client.request("DELETE", f"/user/{_USER_ID}")

    assert response.status_code == 200
    # 13 execute calls: memory_cards select, turn audio keys select,
    # session-open audio key select, 6 deletes, email select,
    # magic_link_tokens delete, consent update, family_accounts delete.
    assert db.execute.await_count == 13
    assert db.commit.await_count >= 6


def test_delete_anonymizes_consent_records_not_deletes_them(db):
    db.execute = AsyncMock(side_effect=_default_execute_results())

    with patch("api.routes.admin.storage.delete_media", new=AsyncMock()):
        client.request("DELETE", f"/user/{_USER_ID}")

    # The 12th execute() call is the consent_records UPDATE (anonymize).
    consent_stmt = db.execute.await_args_list[11].args[0]
    compiled = str(consent_stmt)
    assert "consent_records" in compiled.lower()
    assert "UPDATE" in compiled.upper()


def test_delete_removes_turns(db):
    db.execute = AsyncMock(side_effect=_default_execute_results())

    with patch("api.routes.admin.storage.delete_media", new=AsyncMock()):
        client.request("DELETE", f"/user/{_USER_ID}")

    # 6th execute() call is the turns DELETE (after the three key selects,
    # then memory_cards delete and story_atoms delete).
    turns_stmt = db.execute.await_args_list[5].args[0]
    compiled = str(turns_stmt)
    assert "turns" in compiled.lower()
    assert "DELETE" in compiled.upper()


def test_delete_calls_s3_delete_for_turn_audio_keys(db):
    db.execute = AsyncMock(
        side_effect=_default_execute_results(audio_keys=["audio/a.ogg", "audio/b.ogg"])
    )

    with patch("api.routes.admin.storage.delete_media", new=AsyncMock()) as mock_delete:
        response = client.request("DELETE", f"/user/{_USER_ID}")

    assert response.status_code == 200
    called_keys = {c.args[0] for c in mock_delete.await_args_list}
    assert {"audio/a.ogg", "audio/b.ogg"}.issubset(called_keys)


def test_delete_removes_family_account(db):
    db.execute = AsyncMock(side_effect=_default_execute_results())

    with patch("api.routes.admin.storage.delete_media", new=AsyncMock()):
        client.request("DELETE", f"/user/{_USER_ID}")

    last_stmt = db.execute.await_args_list[-1].args[0]
    compiled = str(last_stmt)
    assert "family_accounts" in compiled.lower()
    assert "DELETE" in compiled.upper()


def test_delete_clears_session_cookie(db):
    db.execute = AsyncMock(side_effect=_default_execute_results())

    with patch("api.routes.admin.storage.delete_media", new=AsyncMock()):
        response = client.request("DELETE", f"/user/{_USER_ID}")

    set_cookie = response.headers.get("set-cookie", "")
    assert "katha_token=" in set_cookie
    assert "Max-Age=0" in set_cookie


def test_delete_continues_after_s3_failure(db):
    db.execute = AsyncMock(
        side_effect=_default_execute_results(s3_keys=["cards/a.png"])
    )

    with patch(
        "api.routes.admin.storage.delete_media",
        new=AsyncMock(side_effect=RuntimeError("S3 down")),
    ):
        response = client.request("DELETE", f"/user/{_USER_ID}")

    # Deletion is best-effort — an S3 failure must not abort the DB cleanup.
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


def test_delete_calls_s3_delete_for_session_open_audio(db):
    """S2.4a: the session-opening voice note has no Turn row, so its key
    lives on the session. Before this sweep existed the object survived
    deletion — the S1.2 consent audit caught it stranded in the bucket."""
    db.execute = AsyncMock(
        side_effect=_default_execute_results(
            session_open_keys=["audio/open-1.ogg", "audio/open-2.ogg"]
        )
    )

    with patch("api.routes.admin.storage.delete_media", new=AsyncMock()) as mock_delete:
        response = client.request("DELETE", f"/user/{_USER_ID}")

    assert response.status_code == 200
    called_keys = {c.args[0] for c in mock_delete.await_args_list}
    assert {"audio/open-1.ogg", "audio/open-2.ogg"} <= called_keys
