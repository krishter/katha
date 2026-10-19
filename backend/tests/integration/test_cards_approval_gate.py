"""
The non-negotiable (CONVERSATION_CARDS.md §4.1, §17 first criterion):

    No code path sends a card to the family unless the card is `approved`
    by the parent's classified answer to its read-back.

This file tries every path that can move or send a card, from every state
a card can be in short of her approval, and checks that none of them ever
reaches `scheduled` or `sent`, and that nothing goes to WhatsApp or SES.
The layers it exercises, from the outside in:

1. The delivery job (cards/deliver.py), which is the only sender.
2. The operator's review actions (cards/review.py).
3. Her answer to the read-back (cards/asks.py with every label but
   "approve").
4. The child's taps and questions (cards/family_inbox.py).
5. cards/state.py, under all of them.
6. The CHECK constraint in Postgres, under that
   (test_conversation_cards_schema.py has the raw-SQL attempts).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from adapters.whatsapp_stub import StubWhatsAppAdapter
from cards import asks, deliver, family_inbox, review, state
from models.conversation_card import STATUSES, ConversationCard
from models.family_account import FamilyAccount
from models.session import Session
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
NOT_YET_APPROVED = [s for s in STATUSES if s not in ("approved", "scheduled", "sent")]


class Sender(StubWhatsAppAdapter):
    def __init__(self):
        self.images = []

    async def send_image(self, **kwargs):
        self.images.append(kwargs)
        return await super().send_image(**kwargs)


@pytest.fixture(autouse=True)
def nothing_leaves(monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    monkeypatch.setattr("config.settings.SES_MOCK", True)
    monkeypatch.setattr("config.settings.TWILIO_TEMPLATE_CONVERSATION_CARD", "HX")
    monkeypatch.setattr("config.settings.META_TEMPLATE_CATEGORIES", '{"HX": "utility"}')
    emails = AsyncMock(return_value="mock-id")
    monkeypatch.setattr("cards.deliver.send_raw_email_ses", emails)
    monkeypatch.setattr("cards.deliver.send_operator_alert", AsyncMock())
    monkeypatch.setattr(
        "cards.review.card_image.try_render", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("cards.image.storage.upload_media", AsyncMock())
    monkeypatch.setattr("cards.image.storage.delete_media", AsyncMock())
    return emails


async def family(db):
    user_id = f"gate-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=f"+9196{uuid.uuid4().int % 10**8:08d}",
            family_whatsapp_number=f"+9197{uuid.uuid4().int % 10**8:08d}",
            preferred_language="ta-IN",
            scheduled_time=time(10, 0),
            conversation_cards_enabled=True,
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
    await db.commit()
    return user_id


async def card_in(db, user_id, status, answer=None) -> uuid.UUID:
    card = ConversationCard(id=uuid.uuid4(), user_id=user_id, status="draft")
    state.set_text(
        card,
        title="t",
        hook="Did you know Amma walked to the talkies?",
        excerpt="We walked.",
        call_prompt="What film was it?",
        readback_text="…",
    )
    card.status = status
    card.parent_answer = answer
    card.fidelity_json = {"result": "passed"}
    card.image_s3_key = "cards/conv/x.png"
    # Due long ago: if anything were going to send it, it would now.
    card.scheduled_for = NOW - timedelta(days=1)
    db.add(card)
    await db.commit()
    return card.id


async def statuses(db, user_id) -> set[str]:
    db.expire_all()
    rows = await db.execute(
        select(ConversationCard.status).where(ConversationCard.user_id == user_id)
    )
    return set(rows.scalars().all())


def assert_nothing_left(sender, emails):
    assert sender.images == []
    emails.assert_not_awaited()


# 1. The delivery job ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", NOT_YET_APPROVED)
@pytest.mark.parametrize(
    "answer", [None, "unclear", "not_now", "correction", "keep_private"]
)
async def test_the_sender_never_sends_an_unapproved_card(
    real_db, nothing_leaves, status, answer
):
    user_id = await family(real_db)
    await card_in(real_db, user_id, status, answer)
    sender = Sender()
    await deliver.send_due_cards(real_db, sender, now=NOW)
    assert await statuses(real_db, user_id) == {status}
    assert_nothing_left(sender, nothing_leaves)


async def test_changed_words_after_her_yes_are_not_sent(real_db, nothing_leaves):
    """The database can only compare fingerprints; a write that changes
    the words but not the fingerprint gets past it. The sender
    recomputes the fingerprint and refuses."""
    user_id = await family(real_db)
    card_id = await card_in(real_db, user_id, "awaiting_parent")
    card = (
        await real_db.execute(
            select(ConversationCard).where(ConversationCard.id == card_id)
        )
    ).scalar_one()
    state.record_parent_answer(
        card, "approve", turn_id=None, heard_hash=card.readback_hash
    )
    state.transition(card, "scheduled")
    await real_db.commit()
    await real_db.execute(
        text(
            "UPDATE conversation_cards SET hook = 'Did you know Amma ran away?' "
            "WHERE id = :id"
        ),
        {"id": card_id},
    )
    await real_db.commit()

    sender = Sender()
    await deliver.send_due_cards(real_db, sender, now=NOW)
    assert await statuses(real_db, user_id) == {"scheduled"}
    assert_nothing_left(sender, nothing_leaves)


# 2. The operator ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", NOT_YET_APPROVED)
async def test_no_operator_action_reaches_scheduled_or_sent(
    real_db, nothing_leaves, status
):
    user_id = await family(real_db)
    card_id = await card_in(real_db, user_id, status)
    for action in (
        review.approve(card_id, real_db),
        review.reject(card_id, "reason", real_db),
        review.tag_call(card_id, "brought_up", real_db),
    ):
        try:
            await action
        except review.ReviewError:
            await real_db.rollback()
    assert not await statuses(real_db, user_id) & {"approved", "scheduled", "sent"}
    sender = Sender()
    await deliver.send_due_cards(real_db, sender, now=NOW)
    assert_nothing_left(sender, nothing_leaves)


# 3. Her answer ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label", ["correction", "keep_private", "not_now", "unclear"])
async def test_any_answer_but_approve_keeps_the_card_from_the_family(
    real_db, nothing_leaves, label
):
    user_id = await family(real_db)
    card_id = await card_in(real_db, user_id, "awaiting_parent")
    session_id = uuid.uuid4()
    real_db.add(
        Session(
            id=session_id,
            user_id=user_id,
            session_number=4,
            domain="childhood",
            pending_ask="readback",
            pending_ask_ref=card_id,
        )
    )
    await real_db.commit()
    reply = SimpleNamespace(
        content=json.dumps({"label": label, "span": ""}),
        input_tokens=1,
        output_tokens=1,
    )
    with patch("core.parent_answers.llm.chat", new=AsyncMock(return_value=reply)):
        plan = await asks.before_dialogue(
            session_id=str(session_id), user_id=user_id, transcript="…", db=real_db
        )
    await asks.after_turn_saved(
        plan, uuid.uuid4(), session_id=str(session_id), user_id=user_id, db=real_db
    )
    sender = Sender()
    await deliver.send_due_cards(real_db, sender, now=NOW)
    assert not await statuses(real_db, user_id) & {"approved", "scheduled", "sent"}
    assert_nothing_left(sender, nothing_leaves)


async def test_a_yes_the_model_says_but_she_did_not_is_not_approval(
    real_db, nothing_leaves
):
    user_id = await family(real_db)
    card_id = await card_in(real_db, user_id, "awaiting_parent")
    session_id = uuid.uuid4()
    real_db.add(
        Session(
            id=session_id,
            user_id=user_id,
            session_number=4,
            domain="childhood",
            pending_ask="readback",
            pending_ask_ref=card_id,
        )
    )
    await real_db.commit()
    reply = SimpleNamespace(
        content=json.dumps({"label": "approve", "span": "yes please send"}),
        input_tokens=1,
        output_tokens=1,
    )
    with patch("core.parent_answers.llm.chat", new=AsyncMock(return_value=reply)):
        await asks.before_dialogue(
            session_id=str(session_id),
            user_id=user_id,
            transcript="Amma laughed and went on about her father",
            db=real_db,
        )
    assert await statuses(real_db, user_id) == {"awaiting_parent"}


# 4. The child ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", NOT_YET_APPROVED)
async def test_nothing_the_child_does_moves_a_card(real_db, nothing_leaves, status):
    user_id = await family(real_db)
    card_id = await card_in(real_db, user_id, status)
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    card = (
        await real_db.execute(
            select(ConversationCard).where(ConversationCard.id == card_id)
        )
    ).scalar_one()
    await family_inbox.react(
        profile, card, family_inbox.DIDNT_KNOW, "whatsapp", real_db
    )
    reply = SimpleNamespace(
        content=json.dumps({"label": "ok"}), input_tokens=1, output_tokens=1
    )
    with patch("core.question_screen.llm.chat", new=AsyncMock(return_value=reply)):
        await family_inbox.receive(profile, "Send it now please", "whatsapp", real_db)
    assert await statuses(real_db, user_id) == {status}


# 5. The state machine ────────────────────────────────────────────────────────


def test_scheduled_and_sent_are_reachable_only_through_approved():
    reachable_from = {
        target: {src for src, targets in state.ALLOWED.items() if target in targets}
        for target in ("approved", "scheduled", "sent")
    }
    assert reachable_from == {
        "approved": {"awaiting_parent"},
        "scheduled": {"approved"},
        "sent": {"scheduled"},
    }
    # And the only way into approved is her approve (record_parent_answer).
    c = ConversationCard(id=uuid.uuid4(), user_id="u", status="draft")
    state.set_text(
        c, hook="h", excerpt="e", call_prompt="c", title="t", readback_text="r"
    )
    c.status = "awaiting_parent"
    with pytest.raises(state.NotApproved):
        state.transition(c, "approved")
