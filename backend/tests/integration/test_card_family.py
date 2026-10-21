"""
The child's side, against real Postgres (CONVERSATION_CARDS.md §8.1, §8.3,
§10, §11, §12 "webhook routing order, including the parent-wins clash",
"deletion cascade and export contents"). WhatsApp is a mock adapter, the
screening call is mocked, and nothing leaves the machine.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from cards import links, state
from core.auth import get_current_user
from main import app
from models.consent_record import ConsentRecord
from models.conversation_card import ConversationCard
from models.db import AsyncSessionLocal, get_db
from models.family_account import FamilyAccount
from models.family_question import FamilyQuestion
from models.session import Session
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cards_on(monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)


def screened(label):
    reply = SimpleNamespace(
        content=json.dumps({"label": label}), input_tokens=1, output_tokens=1
    )
    return patch("core.question_screen.llm.chat", new=AsyncMock(return_value=reply))


@pytest.fixture
def alert():
    with patch("cards.family_inbox.send_operator_alert", new=AsyncMock()) as mock:
        yield mock


def numbers():
    n = uuid.uuid4().int
    return f"+9194{n % 10**8:08d}", f"+9195{(n // 7) % 10**8:08d}"


async def seed(db, *, enabled=True, card_status="sent"):
    user_id = f"fam-{uuid.uuid4().hex[:8]}"
    parent, child = numbers()
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=parent,
            family_whatsapp_number=child,
            preferred_language="ta-IN",
            scheduled_time=time(10, 0),
            conversation_cards_enabled=enabled,
        )
    )
    db.add(
        FamilyAccount(
            email=f"{user_id}@example.com",
            user_id=user_id,
            child_name="Priya",
            parent_title="Amma",
        )
    )
    card = ConversationCard(id=uuid.uuid4(), user_id=user_id, status="draft")
    state.set_text(
        card,
        title="The walk to the talkies",
        hook="Did you know Amma walked to the talkies?",
        excerpt="We walked and told nobody.",
        call_prompt="What do you remember about the film?",
        readback_text="…",
    )
    card.status = "awaiting_parent"
    if card_status in ("sent", "scheduled"):
        state.record_parent_answer(
            card, "approve", turn_id=None, heard_hash=card.readback_hash
        )
        state.transition(card, "scheduled")
        card.scheduled_for = datetime.now(timezone.utc) - timedelta(hours=1)
        if card_status == "sent":
            state.transition(card, "sent")
            card.wa_message_sid = f"SM_CARD_{uuid.uuid4().hex[:6]}"
    elif card_status == "kept_private":
        state.record_parent_answer(card, "keep_private", turn_id=None, heard_hash=None)
    db.add(card)
    await db.commit()
    return SimpleNamespace(
        user_id=user_id,
        parent=parent,
        child=child,
        card_id=card.id,
        wa_sid=card.wa_message_sid,
    )


class Webhook:
    def __init__(self):
        self.adapter = MagicMock()
        self.adapter.validate_signature = MagicMock(return_value=True)
        self.adapter.send_text = AsyncMock(return_value="SM_T")
        self.adapter.send_voice_note = AsyncMock(return_value=("SM_V", "a.ogg"))
        self.adapter.download_voice_note = AsyncMock(return_value=b"audio")

    async def __aenter__(self):
        from adapters.whatsapp_stub import get_whatsapp_adapter

        async def _db():
            async with AsyncSessionLocal() as s:
                yield s

        self._prev = (
            app.dependency_overrides.get(get_db),
            app.dependency_overrides.get(get_whatsapp_adapter),
        )
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_whatsapp_adapter] = lambda: self.adapter
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        return self

    async def __aexit__(self, *exc):
        from adapters.whatsapp_stub import get_whatsapp_adapter

        await self.client.aclose()
        for dep, prev in zip((get_db, get_whatsapp_adapter), self._prev):
            if prev is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = prev

    async def send(self, from_number, **params):
        data = {
            "From": f"whatsapp:{from_number}",
            "MessageSid": f"SM_IN_{uuid.uuid4().hex[:10]}",
            **params,
        }
        return await self.client.post("/webhook/whatsapp", data=data)

    def replies(self):
        return [c.args[1] for c in self.adapter.send_text.await_args_list]


async def questions(db, user_id):
    db.expire_all()
    return list(
        (
            await db.execute(
                select(FamilyQuestion)
                .where(FamilyQuestion.user_id == user_id)
                .order_by(FamilyQuestion.created_at)
            )
        )
        .scalars()
        .all()
    )


async def card_of(db, card_id):
    db.expire_all()
    return (
        await db.execute(select(ConversationCard).where(ConversationCard.id == card_id))
    ).scalar_one()


# ── routing (§10.1) ──────────────────────────────────────────────────────────


async def test_the_childs_number_reaches_the_family_inbox(real_db):
    s = await seed(real_db)
    async with Webhook() as w:
        with screened("ok"):
            await w.send(s.child, Body="Which film was it?")
    [q] = await questions(real_db, s.user_id)
    assert (q.kind, q.screening, q.status, q.channel) == (
        "question",
        "ok",
        "pending",
        "whatsapp",
    )
    assert q.card_id == s.card_id
    assert "I'll ask Amma when we next talk" in w.replies()[0]


async def test_with_cards_off_the_childs_number_is_unknown_as_before(
    real_db, monkeypatch
):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", False)
    s = await seed(real_db)
    async with Webhook() as w:
        await w.send(s.child, Body="Which film was it?")
    from api.routes.webhook import _NOT_SCHEDULED_TEXT

    assert await questions(real_db, s.user_id) == []
    assert w.replies() == [_NOT_SCHEDULED_TEXT]


async def test_a_family_without_cards_is_not_routed(real_db):
    s = await seed(real_db, enabled=False)
    async with Webhook() as w:
        await w.send(s.child, Body="Which film was it?")
    assert await questions(real_db, s.user_id) == []


async def test_the_parent_wins_a_number_clash(real_db):
    """Self-trial: one person as parent and as their own child."""
    s = await seed(real_db)
    other = await seed(real_db)
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == other.user_id)
        )
    ).scalar_one()
    profile.family_whatsapp_number = s.parent  # s's parent is other's "child"
    await real_db.commit()
    async with Webhook() as w:
        with screened("ok"):
            await w.send(s.parent, Body="hello")
    assert await questions(real_db, other.user_id) == []


# ── buttons (§8.1) ───────────────────────────────────────────────────────────


async def test_didnt_know_is_recorded_and_relayed_once(real_db):
    s = await seed(real_db)
    async with Webhook() as w:
        for _ in range(2):
            await w.send(
                s.child,
                ButtonPayload="didnt_know",
                ButtonText="I didn't know this!",
                OriginalRepliedMessageSid=s.wa_sid,
            )
    card = await card_of(real_db, s.card_id)
    assert (card.child_reaction, card.child_reaction_channel) == (
        "didnt_know",
        "whatsapp",
    )
    reactions = [q for q in await questions(real_db, s.user_id) if q.kind == "reaction"]
    assert len(reactions) == 1 and reactions[0].status == "pending"
    assert w.replies()[0] == "Thank you. I'll let Amma know you enjoyed it."


async def test_knew_and_ask(real_db):
    s = await seed(real_db)
    async with Webhook() as w:
        await w.send(s.child, ButtonText="I knew this")
        await w.send(s.child, ButtonPayload="ask")
    assert (await card_of(real_db, s.card_id)).child_reaction == "knew"
    assert await questions(real_db, s.user_id) == []
    assert w.replies() == [
        "Lovely. Amma will be glad it stuck.",
        "What would you like to ask Amma? Type it or send a voice note.",
    ]


# ── screening (§10.2) ────────────────────────────────────────────────────────


async def test_a_sensitive_question_is_held_and_the_child_is_not_told(real_db, alert):
    s = await seed(real_db)
    async with Webhook() as w:
        with screened("sensitive"):
            await w.send(s.child, Body="Who gets the house?")
    [q] = await questions(real_db, s.user_id)
    assert q.status == "held"
    assert "I'll ask Amma" in w.replies()[0]
    alert.assert_awaited_once()


async def test_a_correction_is_not_relayed(real_db):
    s = await seed(real_db)
    async with Webhook() as w:
        with screened("correction"):
            await w.send(s.child, Body="It was Chennai, not Kumbakonam")
    [q] = await questions(real_db, s.user_id)
    assert q.status == "not_relayed"
    assert "don't pass on corrections" in w.replies()[0]


async def test_stop_turns_whatsapp_cards_off(real_db):
    s = await seed(real_db)
    async with Webhook() as w:
        await w.send(s.child, Body="STOP")
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == s.user_id)
        )
    ).scalar_one()
    await real_db.refresh(profile)
    assert profile.card_whatsapp_enabled is False
    assert "no more cards on WhatsApp" in w.replies()[0]


async def test_a_fourth_question_waits(real_db):
    s = await seed(real_db)
    async with Webhook() as w:
        with screened("ok"):
            for i in range(4):
                await w.send(s.child, Body=f"Question {i}?")
    statuses = [q.status for q in await questions(real_db, s.user_id)]
    assert statuses == ["pending", "pending", "pending", "dropped"]
    assert "a few of your questions waiting" in w.replies()[-1]


async def test_a_voice_note_question_is_transcribed_and_kept_as_text(real_db):
    s = await seed(real_db)
    async with Webhook() as w:
        with (
            screened("ok"),
            patch(
                "api.routes.webhook.sarvam_stt.transcribe",
                new=AsyncMock(return_value=SimpleNamespace(transcript="Which film?")),
            ),
        ):
            await w.send(
                s.child, MediaUrl0="https://api.twilio/x", MediaContentType0="audio/ogg"
            )
    [q] = await questions(real_db, s.user_id)
    assert q.transcript == "Which film?" and q.text == "Which film?"


async def test_a_retried_webhook_is_not_queued_twice(real_db):
    s = await seed(real_db)
    async with Webhook() as w:
        with screened("ok"):
            for _ in range(2):
                await w.send(s.child, Body="Which film?", MessageSid="SM_SAME_1")
    assert len(await questions(real_db, s.user_id)) == 1


# ── the email page (§8.3) ────────────────────────────────────────────────────


@pytest.fixture
async def http():
    async def _db():
        async with AsyncSessionLocal() as session:
            yield session

    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    if prev is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = prev


async def test_opening_the_link_records_nothing(real_db, http):
    s = await seed(real_db)
    token = links.make_token(s.card_id)
    page = await http.get(f"/c/{token}?a=didnt_know")
    assert page.status_code == 200
    assert "Did you know Amma walked" in page.text
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert (await card_of(real_db, s.card_id)).child_reaction is None


async def test_the_page_records_a_tap_and_a_question(real_db, http):
    s = await seed(real_db)
    token = links.make_token(s.card_id)
    tapped = await http.post(f"/c/{token}/react")
    assert "let Amma know you enjoyed it" in tapped.text
    with screened("ok"):
        asked = await http.post(f"/c/{token}/ask", data={"question": "Which film?"})
    assert "ask Amma when we next talk" in asked.text
    card = await card_of(real_db, s.card_id)
    assert card.child_reaction_channel == "email"
    kinds = {(q.kind, q.channel) for q in await questions(real_db, s.user_id)}
    assert kinds == {("reaction", "email"), ("question", "email")}


@pytest.mark.parametrize("card_status", ["scheduled", "kept_private"])
async def test_only_a_sent_card_has_a_page(real_db, http, card_status):
    s = await seed(real_db, card_status=card_status)
    token = links.make_token(s.card_id)
    assert (await http.get(f"/c/{token}")).status_code == 404
    assert (await http.post(f"/c/{token}/react")).status_code == 404


async def test_an_expired_link_is_refused(real_db, http):
    s = await seed(real_db)
    old = links.make_token(
        s.card_id, now=datetime.now(timezone.utc) - timedelta(days=31)
    )
    assert (await http.get(f"/c/{old}")).status_code == 404


async def test_one_click_unsubscribe_even_with_cards_off(real_db, http, monkeypatch):
    s = await seed(real_db)
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", False)
    token = links.make_token(s.card_id)
    response = await http.post(
        f"/c/{token}/unsubscribe", data={"List-Unsubscribe": "One-Click"}
    )
    assert response.status_code == 200
    real_db.expire_all()
    family = (
        await real_db.execute(
            select(FamilyAccount).where(FamilyAccount.user_id == s.user_id)
        )
    ).scalar_one()
    assert family.card_email_enabled is False


# ── what the family sees, and deletion (§5.2, §11) ───────────────────────────


@pytest.fixture
async def as_family(http):
    holder = {}
    prev = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: holder["user"]
    yield http, holder
    if prev is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = prev


async def _atoms(db, user_id):
    session_id = uuid.uuid4()
    db.add(
        Session(id=session_id, user_id=user_id, session_number=2, domain="childhood")
    )
    await db.commit()
    for i, private in enumerate((False, True)):
        turn_id = uuid.uuid4()
        db.add(
            Turn(
                id=turn_id,
                session_id=session_id,
                user_id=user_id,
                turn_number=i + 1,
                transcript="…",
                response_text="…",
                is_private=private,
            )
        )
        await db.flush()
        db.add(
            StoryAtom(
                session_id=session_id,
                turn_id=turn_id,
                user_id=user_id,
                domain="childhood",
                title="private one" if private else "shared one",
                narrative="…",
            )
        )
    await db.commit()


async def test_private_atoms_are_hidden_from_the_family(real_db, as_family):
    http, holder = as_family
    s = await seed(real_db)
    await _atoms(real_db, s.user_id)
    holder["user"] = {"user_id": s.user_id, "sub": f"{s.user_id}@example.com"}

    stories = (await http.get("/family/stories")).json()
    titles = [a["title"] for a in stories["stories"]]
    assert titles == ["shared one"]
    stats = (await http.get("/family/stats")).json()
    assert stats["total_story_atoms"] == 1
    export = (await http.get("/family/export")).json()
    assert [a["title"] for a in export["stories"]] == ["shared one"]


async def test_export_has_sent_cards_and_questions_but_never_private_ones(
    real_db, as_family
):
    http, holder = as_family
    s = await seed(real_db)
    private = await seed(real_db, card_status="kept_private")
    card = await card_of(real_db, private.card_id)
    card.user_id = s.user_id
    real_db.add(
        FamilyQuestion(
            user_id=s.user_id, channel="email", raw_text="Which film?", status="pending"
        )
    )
    await real_db.commit()
    holder["user"] = {"user_id": s.user_id, "sub": f"{s.user_id}@example.com"}

    export = (await http.get("/family/export")).json()
    assert [c["status"] for c in export["conversation_cards"]] == ["sent"]
    assert [q["question"] for q in export["family_questions"]] == ["Which film?"]
    with patch(
        "api.routes.family.storage.generate_presigned_url",
        new=AsyncMock(return_value="https://signed/x"),
    ):
        listed = (await http.get("/family/cards")).json()
    assert [c["title"] for c in listed["conversation_cards"]] == [
        "The walk to the talkies"
    ]
    # No source language recorded on this card: not labelled as translated.
    assert listed["conversation_cards"][0]["translated_from"] is None


async def test_deletion_removes_cards_questions_and_images(real_db, as_family):
    http, holder = as_family
    s = await seed(real_db)
    card = await card_of(real_db, s.card_id)
    card.image_s3_key = "cards/conv/a.png"
    real_db.add(FamilyQuestion(user_id=s.user_id, channel="whatsapp", raw_text="?"))
    real_db.add(
        ConsentRecord(
            user_id=s.user_id,
            email_hash="",
            consent_version="share-1.0",
            principal="parent",
        )
    )
    await real_db.commit()
    holder["user"] = {"user_id": s.user_id, "sub": f"{s.user_id}@example.com"}

    with patch("core.erasure.storage.delete_media", new=AsyncMock()) as deleted:
        response = await http.delete(f"/user/{s.user_id}")
    assert response.status_code == 200
    assert "cards/conv/a.png" in {c.args[0] for c in deleted.await_args_list}
    real_db.expire_all()
    for model in (ConversationCard, FamilyQuestion):
        rows = (
            await real_db.execute(select(model).where(model.user_id == s.user_id))
        ).all()
        assert rows == []
