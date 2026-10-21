"""
Sending approved cards, against real Postgres, with the WhatsApp stub and
SES_MOCK (CONVERSATION_CARDS.md §8). Nothing leaves the machine.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from adapters.whatsapp_stub import StubWhatsAppAdapter
from cards import deliver, state
from core.delivery import record_card_delivery
from models.conversation_card import ConversationCard
from models.family_account import FamilyAccount
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


class Recorder(StubWhatsAppAdapter):
    def __init__(self):
        self.sent = []

    async def send_image(self, **kwargs):
        self.sent.append(kwargs)
        return await super().send_image(**kwargs)


@pytest.fixture(autouse=True)
def safe(monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    monkeypatch.setattr("config.settings.SES_MOCK", True)
    monkeypatch.setattr("config.settings.TWILIO_TEMPLATE_CONVERSATION_CARD", "HXcard")
    monkeypatch.setattr(
        "config.settings.META_TEMPLATE_CATEGORIES", '{"HXcard": "utility"}'
    )
    stored = {}

    async def upload(data, key, content_type="audio/ogg"):
        stored[key] = data
        return key

    async def delete(key):
        stored.pop(key, None)

    monkeypatch.setattr("cards.image.storage.upload_media", upload)
    monkeypatch.setattr("cards.image.storage.delete_media", delete)
    return stored


@pytest.fixture
def alert():
    with patch("cards.deliver.send_operator_alert", new=AsyncMock()) as mock:
        yield mock


async def seed(
    db,
    *,
    number="+919000000555",
    email=True,
    wa=True,
    status="scheduled",
    when=NOW - timedelta(minutes=1),
    approved=True,
):
    user_id = f"dlv-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=f"+9193{uuid.uuid4().int % 10**8:08d}",
            family_whatsapp_number=number,
            # The child has proved the number is theirs (P3).
            family_number_confirmed_at=datetime.now(timezone.utc),
            preferred_language="ta-IN",
            scheduled_time=time(10, 0),
            conversation_cards_enabled=True,
            card_whatsapp_enabled=wa,
        )
    )
    db.add(
        FamilyAccount(
            email=f"{user_id}@example.com",
            user_id=user_id,
            child_name="Priya",
            parent_title="Amma",
            timezone="Asia/Kolkata",
            card_email_enabled=email,
        )
    )
    card = ConversationCard(id=uuid.uuid4(), user_id=user_id, status="draft")
    state.set_text(
        card,
        title="The walk to the talkies",
        hook="Did you know Amma walked six kilometres to a film?",
        excerpt="We walked and told nobody.",
        call_prompt="What do you remember about that first film?",
        readback_text="…",
    )
    card.status = "awaiting_parent"
    card.source_language = "ta-IN"
    if approved:
        state.record_parent_answer(
            card, "approve", turn_id=None, heard_hash=card.readback_hash
        )
        card.parent_answered_at = NOW - timedelta(hours=1)
        if status == "scheduled":
            state.transition(card, "scheduled")
            card.scheduled_for = when
    db.add(card)
    await db.commit()
    return user_id, card.id


async def get(db, card_id) -> ConversationCard:
    db.expire_all()
    return (
        await db.execute(select(ConversationCard).where(ConversationCard.id == card_id))
    ).scalar_one()


async def test_a_due_card_goes_on_both_channels(real_db, usage_rows, safe):
    _, card_id = await seed(real_db)
    wa = Recorder()
    assert await deliver.send_due_cards(real_db, wa, now=NOW) == 1

    card = await get(real_db, card_id)
    assert card.status == "sent"
    [send] = wa.sent
    assert send["template_sid"] == "HXcard"
    assert send["to_number"] == "+919000000555"
    assert send["template_variables"] == {
        "2": "Amma",
        "3": "What do you remember about that first film?",
    }
    assert send["operation"] == "outbound_conversation_card"
    assert send["s3_key"] == card.image_s3_key and card.image_s3_key in safe
    assert card.wa_message_sid.startswith("STUB_MSG_")
    assert card.email_status == "accepted" and card.email_message_id.startswith("mock-")
    assert {r["operation"] for r in usage_rows} >= {
        "outbound_conversation_card",
        "card_email",
    }


async def test_not_yet_due_is_not_sent(real_db):
    _, card_id = await seed(real_db, when=NOW + timedelta(minutes=5))
    wa = Recorder()
    assert await deliver.send_due_cards(real_db, wa, now=NOW) == 0
    assert (await get(real_db, card_id)).status == "scheduled" and wa.sent == []


async def test_an_approved_card_is_scheduled_by_the_job_if_it_was_not(real_db):
    _, card_id = await seed(real_db, status="approved")
    await deliver.send_due_cards(real_db, Recorder(), now=NOW)
    card = await get(real_db, card_id)
    assert card.status == "scheduled"
    # No call slots on file: 18:00 child time the day after she approved.
    assert card.scheduled_for == datetime(2026, 10, 22, 12, 30, tzinfo=timezone.utc)


async def test_a_us_number_on_a_marketing_template_gets_email_only(
    real_db, monkeypatch
):
    monkeypatch.setattr(
        "config.settings.META_TEMPLATE_CATEGORIES", '{"HXcard": "marketing"}'
    )
    _, card_id = await seed(real_db, number="+14155550100")
    wa = Recorder()
    await deliver.send_due_cards(real_db, wa, now=NOW)
    card = await get(real_db, card_id)
    assert wa.sent == []
    assert card.wa_skipped_reason == "marketing_us"
    assert card.status == "sent" and card.email_status == "accepted"


async def test_a_child_who_said_stop_and_turned_email_off_gets_nothing(real_db, alert):
    _, card_id = await seed(real_db, wa=False, email=False)
    wa = Recorder()
    await deliver.send_due_cards(real_db, wa, now=NOW)
    card = await get(real_db, card_id)
    assert wa.sent == []
    assert card.status == "scheduled"
    assert card.scheduled_for == NOW + timedelta(days=1)
    alert.assert_awaited_once()


async def test_a_failed_send_is_retried_not_marked_sent(real_db, monkeypatch):
    _, card_id = await seed(real_db, email=False)

    class Broken(Recorder):
        async def send_image(self, **kwargs):
            raise RuntimeError("Twilio down")

    await deliver.send_due_cards(real_db, Broken(), now=NOW)
    card = await get(real_db, card_id)
    assert card.status == "scheduled"
    assert card.wa_error_code == "send_failed"
    assert card.scheduled_for == NOW + timedelta(minutes=30)


async def test_a_third_send_in_seven_days_waits(real_db):
    user_id, card_id = await seed(real_db)
    for days in (1, 3):
        _, other = await seed(real_db)
        sent = await get(real_db, other)
        sent.user_id = user_id
        sent.status = "sent"
        sent.scheduled_for = NOW - timedelta(days=days)
    await real_db.commit()
    wa = Recorder()
    await deliver.send_due_cards(real_db, wa, now=NOW)
    card = await get(real_db, card_id)
    assert card.status == "scheduled" and wa.sent == []
    assert card.scheduled_for >= NOW - timedelta(days=3) + timedelta(days=7)


async def test_a_whatsapp_status_callback_lands_on_the_card(real_db):
    _, card_id = await seed(real_db)
    await deliver.send_due_cards(real_db, Recorder(), now=NOW)
    sid = (await get(real_db, card_id)).wa_message_sid
    assert await record_card_delivery(sid, "delivered", None, real_db)
    card = await get(real_db, card_id)
    assert card.wa_delivery_status == "delivered" and card.wa_delivered_at is not None
