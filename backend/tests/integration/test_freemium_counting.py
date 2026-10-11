"""
What counts as one of the ten free conversations — against a real database.

The unit tests for this can only assert on the shape of the compiled SQL.
The behaviour that actually matters is which rows Postgres returns, and
that is what produced the production failure: a family was told they had
"completed 10 conversations" after two real exchanges, because eight
session rows existed that contained nothing but our own processing errors.
"""

from __future__ import annotations

import uuid

import pytest

from core import freemium, session_manager
from models.session import Session

pytestmark = pytest.mark.asyncio


def _session(user_id: str, *, status: str, exchange_count: int) -> Session:
    return Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=1,
        domain="childhood",
        exchange_count=exchange_count,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
        status=status,
        whatsapp_number="+919876543210",
    )


async def test_failed_sessions_do_not_consume_free_conversations(real_db):
    """The production shape exactly: two sessions that worked, eight that
    opened and failed every turn."""
    user_id = f"user_{uuid.uuid4().hex}"

    for _ in range(2):
        real_db.add(_session(user_id, status="completed", exchange_count=6))
    for _ in range(8):
        # Every message in these failed before record_turn, so
        # exchange_count never left zero. They were abandoned by the stale
        # sweep four hours later.
        real_db.add(_session(user_id, status="abandoned", exchange_count=0))
    await real_db.commit()

    assert await freemium.get_session_count(user_id, real_db) == 2
    assert await freemium.is_session_allowed(user_id, real_db) is True


async def test_an_abandoned_session_she_talked_through_still_counts(real_db):
    """Not keyed on status == 'completed': six exchanges is a conversation
    she had, whether or not the session was ever formally closed."""
    user_id = f"user_{uuid.uuid4().hex}"
    real_db.add(_session(user_id, status="abandoned", exchange_count=6))
    await real_db.commit()

    assert await freemium.get_session_count(user_id, real_db) == 1


async def test_consent_handshake_is_not_a_free_conversation(real_db):
    user_id = f"user_{uuid.uuid4().hex}"
    real_db.add(
        _session(
            user_id,
            status=session_manager.CONSENT_SESSION_STATUS,
            exchange_count=2,
        )
    )
    await real_db.commit()

    assert await freemium.get_session_count(user_id, real_db) == 0


async def test_ten_real_conversations_do_hit_the_limit(real_db):
    """The limit still has to bite — the fix must not make the free tier
    unbounded."""
    user_id = f"user_{uuid.uuid4().hex}"
    for _ in range(freemium.FREE_SESSION_LIMIT):
        real_db.add(_session(user_id, status="completed", exchange_count=4))
    await real_db.commit()

    assert await freemium.get_session_count(user_id, real_db) == 10
    assert await freemium.is_session_allowed(user_id, real_db) is False
