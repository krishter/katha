from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.auth import get_current_user
from main import app
from models.db import get_db

client = TestClient(app)

_USER_ID = "test_user_wa"
_OTHER_USER_ID = "someone_else"


def _fake_current_user():
    return {"sub": "dev@katha.life", "user_id": _USER_ID}


@pytest.fixture
def db():
    """
    Override get_db/get_current_user for the duration of one test, then
    restore whatever was there before — other test modules (test_webhook.py,
    test_conversation.py) register their own module-level get_db override on
    the same shared `app`, and a bare pop() here would delete that override
    for the rest of the pytest session instead of restoring it.
    """
    mock_db = AsyncMock()

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


def _make_profile(name="Subramaniam"):
    profile = MagicMock()
    profile.name = name
    return profile


def _make_atom(**overrides):
    atom = MagicMock()
    atom.id = overrides.get("id", uuid.uuid4())
    atom.user_id = overrides.get("user_id", _USER_ID)
    atom.domain = overrides.get("domain", "childhood")
    atom.title = overrides.get("title", "The Street in Madurai")
    atom.narrative = overrides.get("narrative", "A story about the street.")
    atom.who = overrides.get("who", ["father"])
    atom.what = overrides.get("what", "Daily street life")
    atom.when_approx = overrides.get("when_approx", "circa 1955")
    atom.where_approx = overrides.get("where_approx", "Madurai")
    atom.why = overrides.get("why", "Core childhood memory")
    atom.completeness_score = overrides.get("completeness_score", 4)
    atom.verbatim_quote = overrides.get("verbatim_quote", "It smelled of jasmine.")
    atom.created_at = overrides.get("created_at", datetime.now(timezone.utc))
    return atom


# ── /family/stats ────────────────────────────────────────────────────────────


def test_stats_returns_session_count_and_domain_breakdown(db):
    profile_result = _query_result(scalar_one_or_none=_make_profile())
    account_row = MagicMock(plan="free", onboarding_complete=True)
    plan_result = _query_result(first=account_row)
    session_count_result = _query_result(scalar_one=5)
    domain_counts_result = MagicMock()
    domain_counts_result.all.return_value = [("childhood", 2), ("career", 1)]
    card_result = MagicMock()
    card_result.scalars.return_value.first.return_value = "cards/x.png"
    card_count_result = _query_result(scalar_one=4)

    db.execute = AsyncMock(
        side_effect=[
            profile_result,
            plan_result,
            session_count_result,
            domain_counts_result,
            card_result,
            card_count_result,
        ]
    )

    with patch(
        "api.routes.family.storage.generate_presigned_url",
        new=AsyncMock(return_value="https://signed.example/cards/x.png"),
    ):
        response = client.get("/family/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == _USER_ID
    assert body["user_name"] == "Subramaniam"
    assert body["total_sessions"] == 5
    assert body["total_story_atoms"] == 3
    assert body["domains_covered"] == 2
    assert len(body["domain_breakdown"]) == 8  # all 8 life domains, not just covered
    childhood_row = next(
        d for d in body["domain_breakdown"] if d["domain_id"] == "childhood"
    )
    assert childhood_row["story_count"] == 2
    assert body["latest_card_url"] == "https://signed.example/cards/x.png"
    # Counted for the deletion confirmation copy, which must not invent it.
    assert body["total_memory_cards"] == 4
    assert body["plan"] == "free"
    assert body["session_count"] == 5
    assert body["session_limit"] == 10
    assert body["onboarding_complete"] is True


# ── /family/stories ──────────────────────────────────────────────────────────


def test_list_stories_returns_paginated_results(db):
    atoms = [_make_atom(), _make_atom()]
    total_result = _query_result(scalar_one=2)
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = atoms

    db.execute = AsyncMock(side_effect=[total_result, rows_result])

    response = client.get("/family/stories?page=1&limit=20")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert len(body["stories"]) == 2
    assert body["stories"][0]["domain_label"] == "Childhood & Home"


def test_list_stories_domain_filter_is_applied(db):
    total_result = _query_result(scalar_one=0)
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[total_result, rows_result])

    response = client.get("/family/stories?domain=career")

    assert response.status_code == 200
    assert response.json()["total"] == 0


# ── /family/stories/{id} ─────────────────────────────────────────────────────


def test_get_story_returns_404_for_different_user(db):
    atom = _make_atom(user_id=_OTHER_USER_ID)
    result = _query_result(scalar_one_or_none=atom)
    db.execute = AsyncMock(return_value=result)

    response = client.get(f"/family/stories/{atom.id}")

    assert response.status_code == 404


def test_get_story_returns_story_for_owning_user(db):
    atom = _make_atom(user_id=_USER_ID)
    result = _query_result(scalar_one_or_none=atom)
    db.execute = AsyncMock(return_value=result)

    response = client.get(f"/family/stories/{atom.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(atom.id)


def test_get_story_returns_404_for_malformed_id(db):
    response = client.get("/family/stories/not-a-uuid")
    assert response.status_code == 404


# ── /family/cards ────────────────────────────────────────────────────────────


def test_list_cards_returns_newest_first(db):
    card = MagicMock()
    card.id = uuid.uuid4()
    card.verbatim_quote = "It smelled of jasmine."
    card.domain = "childhood"
    card.image_s3_key = "cards/x.png"
    card.created_at = datetime.now(timezone.utc)

    total_result = _query_result(scalar_one=1)
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = [card]

    db.execute = AsyncMock(side_effect=[total_result, rows_result])

    with patch(
        "api.routes.family.storage.generate_presigned_url",
        new=AsyncMock(return_value="https://signed.example/cards/x.png"),
    ):
        response = client.get("/family/cards")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["cards"][0]["image_url"] == "https://signed.example/cards/x.png"


# ── auth enforcement ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/family/stats",
        "/family/stories",
        f"/family/stories/{uuid.uuid4()}",
        "/family/cards",
    ],
)
def test_routes_return_401_without_valid_cookie(path):
    # No dependency override — real get_current_user runs against a cookie-less request.
    response = client.get(path)
    assert response.status_code == 401


# ── /family/export ───────────────────────────────────────────────────────────
#
# Offered immediately before deletion. Its job is to make the irreversible
# path recoverable, so the shape matters: a user who exports and then
# deletes must still hold their stories.


def _make_session_row(**overrides):
    row = MagicMock()
    row.session_number = overrides.get("session_number", 1)
    row.domain = overrides.get("domain", "childhood")
    row.status = overrides.get("status", "completed")
    row.started_at = overrides.get("started_at", datetime.now(timezone.utc))
    row.ended_at = overrides.get("ended_at", datetime.now(timezone.utc))
    row.exchange_count = overrides.get("exchange_count", 6)
    return row


def _make_card_row():
    card = MagicMock()
    card.verbatim_quote = "It smelled of jasmine."
    card.domain = "childhood"
    card.created_at = datetime.now(timezone.utc)
    return card


def _export_execute_results(atoms=None, sessions=None, cards=None):
    profile_result = _query_result(scalar_one_or_none=_make_profile())
    sessions_result = MagicMock()
    sessions_result.scalars.return_value.all.return_value = sessions or []
    atoms_result = MagicMock()
    atoms_result.scalars.return_value.all.return_value = atoms or []
    cards_result = MagicMock()
    cards_result.scalars.return_value.all.return_value = cards or []
    return [profile_result, sessions_result, atoms_result, cards_result]


def test_export_returns_stories_and_metadata(db):
    db.execute = AsyncMock(
        side_effect=_export_execute_results(
            atoms=[_make_atom()],
            sessions=[_make_session_row()],
            cards=[_make_card_row()],
        )
    )

    response = client.get("/family/export")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert body["profile"]["name"] == "Subramaniam"
    assert len(body["sessions"]) == 1
    assert len(body["memory_cards"]) == 1

    story = body["stories"][0]
    assert story["narrative"] == "A story about the street."
    assert story["verbatim_quote"] == "It smelled of jasmine."
    assert story["where"] == "Madurai"
    assert story["when"] == "circa 1955"


def test_export_is_explicit_that_audio_is_not_included(db):
    """PRD 13.1 promises audio export too. Until that exists the payload
    must say so rather than letting a user believe a JSON file is their
    complete archive before they delete the originals."""
    db.execute = AsyncMock(side_effect=_export_execute_results())

    response = client.get("/family/export")

    assert response.json()["audio_included"] is False


def test_export_requires_authentication():
    """No user_id parameter exists to tamper with — scope comes from the
    JWT, same as deletion."""
    prev = app.dependency_overrides.pop(get_current_user, None)
    try:
        response = client.get("/family/export")
        assert response.status_code == 401
    finally:
        if prev is not None:
            app.dependency_overrides[get_current_user] = prev


def test_export_empty_account_still_returns_valid_shape(db):
    db.execute = AsyncMock(side_effect=_export_execute_results())

    response = client.get("/family/export")

    assert response.status_code == 200
    body = response.json()
    assert body["stories"] == []
    assert body["sessions"] == []
    assert body["memory_cards"] == []
