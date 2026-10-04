"""
The 30-minute nudge must be sent once, not once per scheduler tick.

Production incident 2026-10-02..04: `_send_followups` sent the message and
recorded nothing, so its query stayed true and the job — which runs every
minute — re-sent on every tick. 423 messages went to one number in 72 hours,
about 210 a day, stopping only when the 4-hour stale sweep closed the session.

`tests/test_session_initiator.py::test_followup_sent_for_non_responsive_session`
passed throughout. It hands the scheduler a hand-built list of rows from a
mocked session, so the second call gets the same list the first did and
"did the state change?" is a question it cannot ask. This runs the real
function against real Postgres twice and counts the sends.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from models.session import Session
from models.user_profile import UserProfileModel
from scheduler.session_initiator import _send_followups

pytestmark = pytest.mark.integration


async def _seed_unanswered_session(db, minutes_ago: int = 45) -> tuple[str, uuid.UUID]:
    """A session opened `minutes_ago`, with the parent never having replied."""
    user_id = f"fu-{uuid.uuid4().hex[:8]}"
    session_id = uuid.uuid4()
    started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)

    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Lakshmi",
            whatsapp_number=f"+9198{uuid.uuid4().int % 10**8:08d}",
            preferred_language="ta-IN",
            onboarding_context="",
            family_whatsapp_number="+919000000112",
            scheduled_time=time(9, 30),
            timezone="Asia/Kolkata",
        )
    )
    db.add(
        Session(
            id=session_id,
            user_id=user_id,
            session_number=1,
            domain="childhood",
            status="active",
            started_at=started,
            session_open_message_id="MM_OPEN",
            last_user_message_at=None,
        )
    )
    await db.commit()
    return user_id, session_id


def _adapter() -> MagicMock:
    wa = MagicMock()
    wa.send_text = AsyncMock(return_value="SM_FOLLOWUP")
    return wa


async def test_followup_is_sent_once_across_many_ticks(real_db):
    """Thirty ticks, one message. This is the regression itself."""
    user_id, session_id = await _seed_unanswered_session(real_db)
    wa = _adapter()

    for _ in range(30):
        await _send_followups(real_db, wa)

    assert wa.send_text.await_count == 1, (
        f"{wa.send_text.await_count} follow-ups sent across 30 ticks — the "
        "scheduler runs every minute, so anything above 1 is the production "
        "loop that sent 423 messages in 72 hours"
    )

    real_db.expire_all()
    row = (
        await real_db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one()
    assert row.followup_sent_at is not None, "the send was not recorded"


async def test_followup_is_not_retried_when_the_send_fails(real_db):
    """
    The usual failure is Meta rejecting a free-form message outside the
    24-hour window (63016). That is still true a minute later, so retrying
    rebuilds the loop. The stamp is claimed before sending for this reason.
    """
    _user_id, session_id = await _seed_unanswered_session(real_db)
    wa = MagicMock()
    wa.send_text = AsyncMock(side_effect=RuntimeError("63016 outside window"))

    for _ in range(10):
        await _send_followups(real_db, wa)

    assert wa.send_text.await_count == 1, (
        f"a failing send was retried {wa.send_text.await_count} times"
    )
    real_db.expire_all()
    row = (
        await real_db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one()
    assert row.followup_sent_at is not None


async def test_a_session_answered_in_time_is_never_nudged(real_db):
    """The nudge is for silence. A reply must suppress it."""
    _user_id, session_id = await _seed_unanswered_session(real_db)
    row = (
        await real_db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one()
    row.last_user_message_at = datetime.now(timezone.utc)
    real_db.add(row)
    await real_db.commit()

    wa = _adapter()
    await _send_followups(real_db, wa)
    assert wa.send_text.await_count == 0


async def test_a_fresh_session_is_not_nudged_early(real_db):
    """Under 30 minutes is not silence yet."""
    await _seed_unanswered_session(real_db, minutes_ago=5)
    wa = _adapter()
    await _send_followups(real_db, wa)
    assert wa.send_text.await_count == 0
