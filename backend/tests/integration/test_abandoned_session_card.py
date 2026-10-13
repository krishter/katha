"""
A conversation that simply stops still owes the family a memory card.

This is how most conversations actually end — someone puts the phone down
— and before this the stale sweep was a bare status UPDATE, so those
sessions produced nothing at all: no entity extraction, no card. PRD §7.3
promises a card after each session, and Risk V1 makes that the headline
mitigation for the P0 engagement risk.

Real database because the sweep uses UPDATE ... RETURNING and the
one-card-per-session guard is a real query; neither can be exercised by a
mocked session.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from core import orchestrator, session_manager
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.asyncio


async def _stale_session(db, *, exchanges: int, with_quote: bool = True) -> tuple:
    user_id = f"user_{uuid.uuid4().hex}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Lakshmi",
            whatsapp_number=f"+9199{uuid.uuid4().int % 100000000:08d}",
            family_whatsapp_number="+919876543211",
            preferred_language="ml-IN",
            onboarding_context="",
            scheduled_time=time(9, 30),
        )
    )
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=1,
        domain="childhood",
        exchange_count=exchanges,
        status="active",
        # Older than the four-hour stale threshold.
        started_at=datetime.now(timezone.utc) - timedelta(hours=5),
    )
    db.add(session)
    await db.commit()

    if exchanges:
        turn = Turn(
            session_id=session.id,
            user_id=user_id,
            turn_number=1,
            transcript="We were living near the river at Kumbakonam.",
            detected_language="ml-IN",
            response_text="Tell me more.",
            extraction_json={},
        )
        db.add(turn)
        await db.commit()
        db.add(
            StoryAtom(
                session_id=session.id,
                user_id=user_id,
                domain="childhood",
                narrative="Lived near the river at Kumbakonam.",
                who=[],
                open_threads=[],
                completeness_score=4,
                verbatim_quote=(
                    "the street smelled of jasmine" if with_quote else None
                ),
            )
        )
        await db.commit()
    return user_id, str(session.id)


def _patched_delivery():
    """Everything outside the database: S3 and Twilio."""
    whatsapp = MagicMock()
    whatsapp.send_image = AsyncMock(return_value="SM_CARD")
    return (
        patch("core.orchestrator.storage.upload_media", new=AsyncMock()),
        patch("core.orchestrator.get_whatsapp_adapter", return_value=whatsapp),
        patch("core.orchestrator.run_post_session", new=AsyncMock()),
        whatsapp,
    )


async def _cards(db, user_id) -> list[MemoryCard]:
    result = await db.execute(select(MemoryCard).where(MemoryCard.user_id == user_id))
    return list(result.scalars().all())


async def test_a_session_that_just_stopped_still_produces_a_card(real_db):
    user_id, session_id = await _stale_session(real_db, exchanges=3)

    abandoned = await session_manager.abandon_stale_sessions(real_db)
    assert any(a.session_id == session_id for a in abandoned)

    p1, p2, p3, whatsapp = _patched_delivery()
    with p1, p2, p3:
        processed = await orchestrator.process_abandoned_sessions(abandoned, real_db)

    assert processed >= 1
    cards = await _cards(real_db, user_id)
    assert len(cards) == 1
    whatsapp.send_image.assert_awaited()


async def test_the_session_is_marked_abandoned_not_completed(real_db):
    """The card is owed either way, but a timed-out session is not a
    session the storyteller finished."""
    _, session_id = await _stale_session(real_db, exchanges=2)

    await session_manager.abandon_stale_sessions(real_db)

    row = (
        await real_db.execute(
            select(Session).where(Session.id == uuid.UUID(session_id))
        )
    ).scalar_one()
    assert row.status == "abandoned"
    assert row.ended_reason == "timeout"


async def test_a_session_nobody_replied_to_produces_nothing(real_db):
    """Katha opened it and no one answered. exchange_count is incremented
    only after a turn fully succeeds, so zero means there is no
    conversation to make a card from."""
    user_id, _ = await _stale_session(real_db, exchanges=0)

    abandoned = await session_manager.abandon_stale_sessions(real_db)

    p1, p2, p3, whatsapp = _patched_delivery()
    with p1, p2, p3:
        processed = await orchestrator.process_abandoned_sessions(abandoned, real_db)

    assert processed == 0
    assert await _cards(real_db, user_id) == []
    whatsapp.send_image.assert_not_awaited()


async def test_processing_twice_does_not_make_a_second_card(real_db):
    """The extraction-triggered close races the sweep; the family must
    never get the same memory twice."""
    user_id, _ = await _stale_session(real_db, exchanges=3)
    abandoned = await session_manager.abandon_stale_sessions(real_db)

    p1, p2, p3, _ = _patched_delivery()
    with p1, p2, p3:
        await orchestrator.process_abandoned_sessions(abandoned, real_db)
        await orchestrator.process_abandoned_sessions(abandoned, real_db)

    assert len(await _cards(real_db, user_id)) == 1
