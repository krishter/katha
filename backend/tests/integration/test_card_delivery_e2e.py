"""
Delivery status against a real database.

record_card_delivery finds a card by the Twilio SID, which is a real query
against a real index — a mocked session cannot tell whether that lookup
actually matches, and the lookup is the whole mechanism.
"""

from __future__ import annotations

import uuid

import pytest

from core.delivery import record_card_delivery
from models.memory_card import MemoryCard
from models.session import Session

pytestmark = pytest.mark.asyncio


async def _card(db, sid: str) -> MemoryCard:
    user_id = f"user_{uuid.uuid4().hex}"
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=1,
        domain="childhood",
        status="completed",
    )
    db.add(session)
    await db.commit()

    card = MemoryCard(
        session_id=session.id,
        user_id=user_id,
        verbatim_quote="the street smelled of jasmine",
        domain="childhood",
        image_s3_key="cards/x.png",
        twilio_message_sid=sid,
        delivery_status="queued",
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return card


async def test_delivered_status_is_written_to_the_right_card(real_db):
    card = await _card(real_db, "SM-delivered")
    await _card(real_db, "SM-other")

    assert await record_card_delivery("SM-delivered", "delivered", None, real_db)

    await real_db.refresh(card)
    assert card.delivery_status == "delivered"
    assert card.delivered_at is not None


async def test_63016_is_recorded_rather_than_silently_lost(real_db):
    """The production failure: accepted by Twilio, rejected by Meta, and
    previously invisible everywhere."""
    card = await _card(real_db, "SM-rejected")

    await record_card_delivery("SM-rejected", "failed", "63016", real_db)

    await real_db.refresh(card)
    assert card.delivery_status == "failed"
    assert card.delivery_error_code == "63016"
    assert card.delivered_at is None


async def test_a_card_is_not_marked_delivered_merely_by_being_sent(real_db):
    card = await _card(real_db, "SM-sent")
    await record_card_delivery("SM-sent", "sent", None, real_db)
    await real_db.refresh(card)
    assert card.delivered_at is None


async def test_an_unknown_sid_matches_nothing(real_db):
    await _card(real_db, "SM-known")
    assert await record_card_delivery("SM-unknown", "delivered", None, real_db) is False
