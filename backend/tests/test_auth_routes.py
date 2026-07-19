from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from models.db import get_db

client = TestClient(app, follow_redirects=False)


@pytest.fixture
def db():
    mock_db = AsyncMock()

    async def _override_get_db():
        yield mock_db

    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield mock_db
    finally:
        if prev is not None:
            app.dependency_overrides[get_db] = prev
        else:
            app.dependency_overrides.pop(get_db, None)


def test_magic_link_request_always_returns_200(db):
    with patch("api.routes.auth.auth.send_magic_link", new=AsyncMock()):
        response = client.post("/auth/magic-link", data={"email": "dev@katha.life"})
    assert response.status_code == 200
    assert "message" in response.json()


def test_magic_link_request_returns_200_even_on_internal_error(db):
    with patch(
        "api.routes.auth.auth.send_magic_link",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = client.post("/auth/magic-link", data={"email": "dev@katha.life"})
    assert response.status_code == 200


def test_verify_sets_cookie_and_redirects_to_dashboard(db):
    onboarding_complete_result = MagicMock()
    onboarding_complete_result.scalar_one_or_none.return_value = True
    db.execute = AsyncMock(return_value=onboarding_complete_result)

    with (
        patch(
            "api.routes.auth.auth.verify_magic_link",
            new=AsyncMock(return_value=("dev@katha.life", "test_user_wa")),
        ),
        patch("api.routes.auth.auth.create_jwt", return_value="fake.jwt.token"),
    ):
        response = client.get("/auth/verify", params={"token": "real-token"})

    assert response.status_code == 302
    assert response.headers["location"].endswith("/family")
    assert "katha_token=fake.jwt.token" in response.headers.get("set-cookie", "")


def test_verify_redirects_to_onboarding_when_incomplete(db):
    onboarding_complete_result = MagicMock()
    onboarding_complete_result.scalar_one_or_none.return_value = False
    db.execute = AsyncMock(return_value=onboarding_complete_result)

    with (
        patch(
            "api.routes.auth.auth.verify_magic_link",
            new=AsyncMock(return_value=("dev@katha.life", "test_user_wa")),
        ),
        patch("api.routes.auth.auth.create_jwt", return_value="fake.jwt.token"),
    ):
        response = client.get("/auth/verify", params={"token": "real-token"})

    assert response.status_code == 302
    assert response.headers["location"].endswith("/family/onboarding")


def test_verify_redirects_to_frontend_error_state_for_bad_token(db):
    with patch(
        "api.routes.auth.auth.verify_magic_link",
        new=AsyncMock(side_effect=HTTPException(status_code=400)),
    ):
        response = client.get("/auth/verify", params={"token": "bogus"})

    assert response.status_code == 302
    location = response.headers["location"]
    assert "/family/auth/verify" in location
    assert "error=expired" in location
    # Must not leak a raw JSON error body to the browser on a full navigation.
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}


def test_logout_deletes_cookie_and_redirects_to_login():
    response = client.post("/auth/logout")

    assert response.status_code == 302
    assert response.headers["location"].endswith("/family/login")
    set_cookie = response.headers.get("set-cookie", "")
    assert "katha_token=" in set_cookie
    assert "Max-Age=0" in set_cookie
