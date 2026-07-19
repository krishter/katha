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


def _default_execute_results(s3_keys=None, email="dev@katha.life"):
    """
    Build the ordered list of db.execute() return values matching
    admin.delete_user's call sequence:
    1. select memory_cards.image_s3_key
    2-6. delete memory_cards / story_atoms / facts / sessions / user_profiles
    7. select family_accounts.email
    8. delete magic_link_tokens (only if email found)
    9. update consent_records
    10. delete family_accounts
    """
    s3_key_result = MagicMock()
    s3_key_result.scalars.return_value.all.return_value = s3_keys or []

    email_result = MagicMock()
    email_result.scalar_one_or_none.return_value = email

    generic = lambda: MagicMock()  # noqa: E731 — result of a DELETE/UPDATE, unused

    results = [s3_key_result]
    results += [generic() for _ in range(5)]  # the 5 DELETE-by-user_id statements
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
    # 10 execute calls: memory_cards select, 5 deletes, email select,
    # magic_link_tokens delete, consent update, family_accounts delete.
    assert db.execute.await_count == 10
    assert db.commit.await_count >= 5


def test_delete_anonymizes_consent_records_not_deletes_them(db):
    db.execute = AsyncMock(side_effect=_default_execute_results())

    with patch("api.routes.admin.storage.delete_media", new=AsyncMock()):
        client.request("DELETE", f"/user/{_USER_ID}")

    # The 9th execute() call is the consent_records UPDATE (anonymize).
    consent_stmt = db.execute.await_args_list[8].args[0]
    compiled = str(consent_stmt)
    assert "consent_records" in compiled.lower()
    assert "UPDATE" in compiled.upper()


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
