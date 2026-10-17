"""
C2 (docs/specs/COST_TO_SERVE.md §7): Layer 3 is built on a session's first
turn, stored on the session, and reused for the rest of it — against real
Postgres, since the snapshot is a JSONB round trip.
"""

from __future__ import annotations

import uuid
from datetime import time

import pytest
from sqlalchemy import select

from core import orchestrator
from memory import fact_store
from models.session import Session
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration


async def _user_and_session(db, user_id: str) -> str:
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Lakshmi",
            whatsapp_number=f"+91{uuid.uuid4().int % 10**10:010d}",
            preferred_language="ta-IN",
            onboarding_context="",
            scheduled_time=time(10, 30),
        )
    )
    session_id = uuid.uuid4()
    db.add(
        Session(
            id=session_id,
            user_id=user_id,
            session_number=4,
            domain="childhood",
            status="active",
        )
    )
    await db.commit()
    return str(session_id)


async def _snapshot(db, session_id: str):
    db.expire_all()
    return (
        await db.execute(
            select(Session.prior_context).where(Session.id == uuid.UUID(session_id))
        )
    ).scalar_one()


async def test_snapshot_written_on_turn_one_and_reused_on_turn_two(real_db):
    user_id = f"freeze-{uuid.uuid4().hex[:8]}"
    session_id = await _user_and_session(real_db, user_id)
    await fact_store.update_facts(
        user_id, {"people": [{"name": "Kamala", "relationship": "sister"}]}, real_db
    )
    await fact_store.upsert_significant_person(
        user_id,
        {"name": "Mr. Iyer", "relationship": "teacher", "why_significant": "patient"},
        real_db,
    )

    first = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )
    stored = await _snapshot(real_db, session_id)
    assert stored["facts"] == {"people": [{"name": "Kamala", "relationship": "sister"}]}
    assert [p["name"] for p in stored["significant_people"]] == ["Mr. Iyer"]

    # Something new is learned mid-session...
    await fact_store.update_facts(user_id, {"places": ["Madurai"]}, real_db)

    # ...and the rest of the session still sees the snapshot, unchanged.
    second = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )
    assert second == first
    assert "places" not in second.facts


async def test_a_session_from_before_the_snapshot_is_filled_on_its_next_turn(
    real_db,
):
    user_id = f"freeze-{uuid.uuid4().hex[:8]}"
    session_id = await _user_and_session(real_db, user_id)
    assert await _snapshot(real_db, session_id) is None

    await fact_store.update_facts(user_id, {"places": ["Thrissur"]}, real_db)
    context = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )

    assert context.facts == {"places": ["Thrissur"]}
    assert (await _snapshot(real_db, session_id))["facts"] == {"places": ["Thrissur"]}


async def test_flag_off_rebuilds_every_turn_and_stores_nothing(real_db, monkeypatch):
    monkeypatch.setattr("core.orchestrator.settings.LAYER3_FREEZE_PER_SESSION", False)
    user_id = f"freeze-{uuid.uuid4().hex[:8]}"
    session_id = await _user_and_session(real_db, user_id)

    await fact_store.update_facts(user_id, {"places": ["Madurai"]}, real_db)
    first = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )
    await fact_store.update_facts(user_id, {"dates": ["1958"]}, real_db)
    second = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )

    assert "dates" not in first.facts
    assert second.facts["dates"] == ["1958"]
    assert await _snapshot(real_db, session_id) is None


async def test_a_failed_snapshot_read_still_gives_the_turn_a_layer_3(
    real_db, monkeypatch
):
    user_id = f"freeze-{uuid.uuid4().hex[:8]}"
    session_id = "not-a-uuid"  # makes the snapshot lookup itself fail
    await _user_and_session(real_db, user_id)
    await fact_store.update_facts(user_id, {"places": ["Madurai"]}, real_db)

    context = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )
    assert context.facts == {"places": ["Madurai"]}
