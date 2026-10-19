"""
The operator's card review page and its actions, over HTTP against real
Postgres (CONVERSATION_CARDS.md §7.6). The fidelity check on an edit is the
only model call, and is mocked.
"""

from __future__ import annotations

import base64
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from cards import image as card_image
from cards import review, state
from main import app
from models.conversation_card import ConversationCard
from models.db import AsyncSessionLocal, get_db
from models.family_account import FamilyAccount
from models.family_question import FamilyQuestion
from models.session import Session
from models.turn import Turn

pytestmark = pytest.mark.integration

TOKEN = "t" * 40
BASIC = base64.b64encode(f"krish:{TOKEN}".encode()).decode()
AUTH = {"Authorization": f"Basic {BASIC}"}


@pytest.fixture(autouse=True)
def operator(monkeypatch):
    monkeypatch.setattr("config.settings.ADMIN_API_TOKEN", TOKEN)
    stored = {}

    async def upload(data, key, content_type="audio/ogg"):
        stored[key] = data
        return key

    async def presign(key, expires_in=900):
        return f"https://s3.test/{key}?sig"

    async def delete(key):
        stored.pop(key, None)

    monkeypatch.setattr("cards.image.storage.upload_media", upload)
    monkeypatch.setattr("cards.image.storage.delete_media", delete)
    monkeypatch.setattr(
        "api.routes.admin_cards.storage.generate_presigned_url", presign
    )
    return stored


@pytest.fixture
async def client():
    async def _override():
        async with AsyncSessionLocal() as session:
            yield session

    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        if prev is not None:
            app.dependency_overrides[get_db] = prev
        else:
            app.dependency_overrides.pop(get_db, None)


async def make_card(db, *, status="operator_review", passed=True, image=True, **text):
    user_id = f"rev-{uuid.uuid4().hex[:8]}"
    session_id, turn_id = uuid.uuid4(), uuid.uuid4()
    db.add(
        Session(id=session_id, user_id=user_id, session_number=2, domain="childhood")
    )
    db.add(
        FamilyAccount(
            email=f"{user_id}@example.com", user_id=user_id, parent_title="Amma"
        )
    )
    await db.commit()
    db.add(
        Turn(
            id=turn_id,
            session_id=session_id,
            user_id=user_id,
            turn_number=1,
            transcript="We walked to the talkies <b>and</b> told nobody.",
            response_text="…",
        )
    )
    card_id = uuid.uuid4()
    card = ConversationCard(
        id=card_id, user_id=user_id, status="draft", source_turn_ids=[turn_id]
    )
    state.set_text(
        card,
        title="The walk to the talkies",
        hook=text.get("hook", "Did you know Amma walked six kilometres to a film?"),
        excerpt="We walked six kilometres and told nobody.",
        call_prompt="What do you remember about that first film?",
        readback_text="நீங்கள் நடந்து போனீர்கள்",
        held_back_note="Left out which film.",
    )
    card.status = status
    card.fidelity_json = {"result": "passed" if passed else "failed", "sentences": []}
    card.image_s3_key = "cards/conv/x.png" if image else None
    db.add(card)
    await db.commit()
    return SimpleNamespace(card_id=card_id, user_id=user_id, turn_id=str(turn_id))


async def reload(db, card_id) -> ConversationCard:
    db.expire_all()
    return (
        await db.execute(select(ConversationCard).where(ConversationCard.id == card_id))
    ).scalar_one()


def form(**fields):
    return {"csrf": review.csrf_token(), **fields}


# ── auth ─────────────────────────────────────────────────────────────────────


async def test_without_the_token_the_page_asks_for_it(client):
    response = await client.get("/admin/cards/view")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


async def test_with_no_token_configured_it_does_not_exist(client, monkeypatch):
    monkeypatch.setattr("config.settings.ADMIN_API_TOKEN", "")
    assert (await client.get("/admin/cards/view", headers=AUTH)).status_code == 404


async def test_a_post_without_the_csrf_token_is_refused(real_db, client):
    """Basic credentials ride along on a form another site submits."""
    made = await make_card(real_db)
    response = await client.post(
        f"/admin/cards/{made.card_id}/approve", headers=AUTH, data={"csrf": "guess"}
    )
    assert response.status_code == 403
    assert (await reload(real_db, made.card_id)).status == "operator_review"


# ── the page ─────────────────────────────────────────────────────────────────


async def test_the_page_shows_the_card_its_sources_and_escapes_them(real_db, client):
    made = await make_card(real_db)
    page = await client.get("/admin/cards/view", headers=AUTH)
    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    html = page.text
    assert "The walk to the talkies" in html
    assert "Left out which film." in html
    assert "https://s3.test/cards/conv/x.png?sig" in html
    assert made.turn_id in html
    # Her words are shown, escaped.
    assert "&lt;b&gt;and&lt;/b&gt;" in html and "<b>and</b>" not in html
    assert review.csrf_token() in html


# ── approve / reject ─────────────────────────────────────────────────────────


async def test_approve_moves_it_to_her_and_no_further(real_db, client):
    made = await make_card(real_db)
    response = await client.post(
        f"/admin/cards/{made.card_id}/approve", headers=AUTH, data=form()
    )
    assert response.status_code == 303
    card = await reload(real_db, made.card_id)
    assert card.status == "awaiting_parent"
    assert card.operator_action == "approve"
    assert card.operator_reviewed_at is not None
    assert card.parent_answer is None


async def test_a_card_that_failed_its_check_cannot_be_approved(real_db, client):
    made = await make_card(real_db, passed=False)
    response = await client.post(
        f"/admin/cards/{made.card_id}/approve", headers=AUTH, data=form()
    )
    assert "fidelity" in response.headers["location"]
    assert (await reload(real_db, made.card_id)).status == "operator_review"


async def test_approve_makes_a_missing_image(real_db, client, operator):
    made = await make_card(real_db, image=False)
    await client.post(f"/admin/cards/{made.card_id}/approve", headers=AUTH, data=form())
    card = await reload(real_db, made.card_id)
    assert card.status == "awaiting_parent"
    assert card.image_s3_key in operator


@pytest.mark.parametrize(
    "status", ["draft", "awaiting_parent", "rejected", "kept_private", "withdrawn"]
)
async def test_only_a_card_in_review_can_be_approved(real_db, client, status):
    made = await make_card(real_db, status=status)
    await client.post(f"/admin/cards/{made.card_id}/approve", headers=AUTH, data=form())
    assert (await reload(real_db, made.card_id)).status == status


async def test_reject_needs_and_keeps_a_reason(real_db, client):
    made = await make_card(real_db)
    await client.post(
        f"/admin/cards/{made.card_id}/reject", headers=AUTH, data=form(reason="  ")
    )
    assert (await reload(real_db, made.card_id)).status == "operator_review"
    await client.post(
        f"/admin/cards/{made.card_id}/reject",
        headers=AUTH,
        data=form(reason="Hook gives away the ending"),
    )
    card = await reload(real_db, made.card_id)
    assert card.status == "rejected"
    assert card.operator_reason == "Hook gives away the ending"


# ── edit ─────────────────────────────────────────────────────────────────────


def _check(verdict: str):
    payload = {
        "sentences": [
            {
                "field": "hook",
                "text": "x",
                "verdict": verdict,
                "turn_ids": [],
                "reason": "",
            },
            {"field": "excerpt", "text": "y", "verdict": "connective"},
        ]
    }
    return AsyncMock(
        return_value=SimpleNamespace(
            content=json.dumps(payload), input_tokens=1, output_tokens=1
        )
    )


async def test_an_edit_is_checked_again_and_re_rendered(real_db, client, operator):
    made = await make_card(real_db)
    before = await reload(real_db, made.card_id)
    old_hash = before.readback_hash
    with patch("cards.write.llm.chat", new=_check("connective")) as chat:
        response = await client.post(
            f"/admin/cards/{made.card_id}/edit",
            headers=AUTH,
            data=form(hook="Did you know Amma once walked six kilometres to a film?"),
        )
    assert chat.await_args.kwargs["purpose"] == "card_fidelity"
    assert response.headers["location"] == "/admin/cards/view"
    card = await reload(real_db, made.card_id)
    assert card.status == "operator_review"  # still needs an explicit approve
    assert card.operator_edited is True
    assert card.readback_hash != old_hash
    assert card.fidelity_json["result"] == "passed"
    assert card.image_s3_key in operator


async def test_an_edit_that_fails_its_check_blocks_approval(real_db, client):
    made = await make_card(real_db)
    with patch("cards.write.llm.chat", new=_check("unsupported")):
        response = await client.post(
            f"/admin/cards/{made.card_id}/edit",
            headers=AUTH,
            data=form(hook="Did you know Amma ran away from home?"),
        )
    assert (
        "did+not+pass" in response.headers["location"]
        or "did%20not%20pass" in (response.headers["location"])
    )
    card = await reload(real_db, made.card_id)
    assert card.fidelity_json["result"] == "failed"
    assert not card_image.is_current(card)
    await client.post(f"/admin/cards/{made.card_id}/approve", headers=AUTH, data=form())
    assert (await reload(real_db, made.card_id)).status == "operator_review"


async def test_once_she_has_it_the_operator_cannot_change_it(real_db, client):
    made = await make_card(real_db, status="awaiting_parent")
    await client.post(
        f"/admin/cards/{made.card_id}/edit",
        headers=AUTH,
        data=form(hook="Did you know something else?"),
    )
    card = await reload(real_db, made.card_id)
    assert card.hook == "Did you know Amma walked six kilometres to a film?"


# ── held questions and the call tag ──────────────────────────────────────────


async def test_a_held_question_is_released_or_dropped(real_db, client):
    ids = []
    for _ in range(2):
        q = FamilyQuestion(
            id=uuid.uuid4(),
            user_id="rev-q",
            channel="whatsapp",
            raw_text="Who gets the house?",
            screening="sensitive",
            status="held",
        )
        ids.append(q.id)
        real_db.add(q)
    await real_db.commit()

    page = await client.get("/admin/cards/view", headers=AUTH)
    assert "Who gets the house?" in page.text
    await client.post(f"/admin/questions/{ids[0]}/release", headers=AUTH, data=form())
    await client.post(f"/admin/questions/{ids[1]}/drop", headers=AUTH, data=form())
    real_db.expire_all()
    statuses = [
        (
            await real_db.execute(
                select(FamilyQuestion.status).where(FamilyQuestion.id == i)
            )
        ).scalar_one()
        for i in ids
    ]
    assert statuses == ["released", "dropped"]


async def test_only_a_sent_card_can_be_tagged(real_db, client):
    made = await make_card(real_db, status="awaiting_parent")
    response = await client.post(
        f"/admin/cards/{made.card_id}/tag", headers=AUTH, data=form(tag="brought_up")
    )
    assert "sent" in response.headers["location"]
    assert (await reload(real_db, made.card_id)).call_tag is None
