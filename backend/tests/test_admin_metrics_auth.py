"""
Operator auth for /admin/metrics and /admin/metrics/view
(docs/specs/ADMIN_METRICS.md §4 and §9).
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from core import metrics
from main import app
from models.db import get_db

_TOKEN = "t" * 40
client = TestClient(app)


def _empty_result():
    result = MagicMock()
    result.__iter__ = lambda self: iter([])
    result.scalar_one.return_value = 0
    return result


@pytest.fixture
def db(monkeypatch):
    """A database with nothing in it, so the routes render real (empty) metrics."""
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=_empty_result())

    async def _override():
        yield mock_db

    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    monkeypatch.setattr("config.settings.ADMIN_API_TOKEN", _TOKEN)
    try:
        yield mock_db
    finally:
        if prev is not None:
            app.dependency_overrides[get_db] = prev
        else:
            app.dependency_overrides.pop(get_db, None)


def _basic(token: str, user: str = "krish") -> dict:
    raw = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", ["/admin/metrics", "/admin/metrics/view"])
def test_routes_do_not_exist_without_a_configured_token(db, monkeypatch, path):
    monkeypatch.setattr("config.settings.ADMIN_API_TOKEN", "")
    response = client.get(path, headers=_bearer(_TOKEN))
    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/admin/metrics", "/admin/metrics/view"])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Basic not-base64!!"},
        {"Authorization": "Token " + "t" * 40},
    ],
)
def test_missing_or_wrong_token_is_401(db, path, headers):
    response = client.get(path, headers=headers)
    assert response.status_code == 401


def test_view_asks_the_browser_to_prompt(db):
    response = client.get("/admin/metrics/view")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="katha-admin"'


def test_json_route_does_not_prompt(db):
    response = client.get("/admin/metrics")
    assert "WWW-Authenticate" not in response.headers


def test_correct_bearer_gets_json(db):
    response = client.get("/admin/metrics", headers=_bearer(_TOKEN))
    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "Asia/Kolkata"
    assert body["stories"] == metrics.STORIES_PLACEHOLDER


def test_correct_basic_gets_html(db):
    response = client.get("/admin/metrics/view", headers=_basic(_TOKEN))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Katha pilot metrics" in response.text


@pytest.mark.parametrize("path", ["/admin/metrics", "/admin/metrics/view"])
def test_responses_are_not_cached_or_indexed(db, path):
    response = client.get(path, headers=_basic(_TOKEN))
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Robots-Tag"] == "noindex"


def test_a_logged_in_family_is_still_401(db):
    """The family session cookie and JWT are never accepted here."""
    from core.auth import create_jwt

    family_jwt = create_jwt("family@example.invalid", "user_abc")
    family = TestClient(app, cookies={"katha_token": family_jwt})
    assert family.get("/admin/metrics").status_code == 401
    assert family.get("/admin/metrics", headers=_bearer(family_jwt)).status_code == 401


def test_the_transaction_is_made_read_only_first(db):
    client.get("/admin/metrics", headers=_bearer(_TOKEN))
    first = db.execute.await_args_list[0].args[0]
    assert str(first) == "SET TRANSACTION READ ONLY"
    db.rollback.assert_awaited()


def test_refuses_to_run_unprotected(db):
    db.execute = AsyncMock(side_effect=RuntimeError("cannot set read only"))
    response = client.get("/admin/metrics", headers=_bearer(_TOKEN))
    assert response.status_code == 500


def test_excluded_ids_are_parsed_from_the_setting():
    assert metrics.parse_excluded(" user_a, ,user_b,") == frozenset(
        {"user_a", "user_b"}
    )
    assert metrics.parse_excluded("") == frozenset()
