"""
Conversation cards against real Postgres (docs/specs/CONVERSATION_CARDS.md
§4.1, §11): the database refuses to hold a card as approved, scheduled or
sent without her "approve" on its exact current text, whatever code
writes it; and a sharing answer is never read as recording consent.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core import parent_consent
from models.consent_record import ConsentRecord
from models.conversation_card import ConversationCard

pytestmark = pytest.mark.integration


async def _card(db, **fields) -> ConversationCard:
    card = ConversationCard(user_id=f"card-{uuid.uuid4().hex[:8]}", **fields)
    db.add(card)
    await db.commit()
    return card


@pytest.mark.parametrize("status", ["approved", "scheduled", "sent"])
@pytest.mark.parametrize(
    "answer, approved_hash",
    [
        (None, None),
        ("unclear", "h1"),
        ("correction", "h1"),
        ("keep_private", "h1"),
        # Her yes, but to different words than the card now holds.
        ("approve", "an-older-text"),
        ("approve", None),
    ],
)
async def test_the_database_refuses_an_unapproved_card_leaving(
    real_db, status, answer, approved_hash
):
    card = await _card(
        real_db,
        status="awaiting_parent",
        readback_hash="h1",
        parent_answer=answer,
        approved_hash=approved_hash,
    )
    # Raw SQL: no ORM, no cards/state.py, nothing in the way but the
    # constraint itself.
    with pytest.raises(IntegrityError, match="ck_conversation_cards_parent_approved"):
        await real_db.execute(
            text("UPDATE conversation_cards SET status = :s WHERE id = :id"),
            {"s": status, "id": card.id},
        )
    await real_db.rollback()


async def test_an_approved_card_can_be_scheduled_and_sent(real_db):
    card = await _card(
        real_db,
        status="awaiting_parent",
        readback_hash="h1",
    )
    await real_db.execute(
        text(
            "UPDATE conversation_cards SET parent_answer = 'approve', "
            "approved_hash = 'h1', status = 'approved' WHERE id = :id"
        ),
        {"id": card.id},
    )
    for status in ("scheduled", "sent"):
        await real_db.execute(
            text("UPDATE conversation_cards SET status = :s WHERE id = :id"),
            {"s": status, "id": card.id},
        )
    await real_db.commit()

    # Changing the words after her yes is refused too.
    with pytest.raises(IntegrityError, match="ck_conversation_cards_parent_approved"):
        await real_db.execute(
            text("UPDATE conversation_cards SET readback_hash = 'h2' WHERE id = :id"),
            {"id": card.id},
        )
    await real_db.rollback()


async def test_an_unknown_status_is_refused(real_db):
    with pytest.raises(IntegrityError, match="ck_conversation_cards_status"):
        await _card(real_db, status="delivered")
    await real_db.rollback()


async def test_sharing_records_do_not_touch_recording_consent(real_db):
    user_id = f"share-{uuid.uuid4().hex[:8]}"
    for version in (parent_consent.PARENT_CONSENT_VERSION, "share-1.0-never"):
        real_db.add(
            ConsentRecord(
                user_id=user_id,
                email_hash="",
                consent_version=version,
                principal=parent_consent.PRINCIPAL_PARENT,
            )
        )
        await real_db.commit()

    assert await parent_consent.has_granted(user_id, real_db)
