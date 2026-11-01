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


async def test_a_person_rejected_mid_session_leaves_the_frozen_snapshot(real_db):
    """The live case: Divya was in this session's Layer 3 snapshot, the
    parent said "I never said Divya", and Katha raised her again at the
    close because the snapshot is frozen. A rejection must reach the turns
    still to come in the same session."""
    user_id = f"freeze-{uuid.uuid4().hex[:8]}"
    session_id = await _user_and_session(real_db, user_id)
    await fact_store.update_facts(
        user_id,
        {
            "people": [
                {"name": "ദിവ്യ (Divya)", "relationship": "former student"},
                {"name": "നേഹ (Neha)", "relationship": "former student"},
            ]
        },
        real_db,
    )
    for name in ("Divya", "Neha"):
        await fact_store.upsert_significant_person(
            user_id,
            {"name": name, "relationship": "student", "why_significant": "cried"},
            real_db,
        )

    first = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )
    assert {p["name"] for p in first.significant_people} == {"Divya", "Neha"}

    await fact_store.mark_rejected(user_id, "ദിവ്യ (Divya)", real_db)

    later = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )
    assert [p["name"] for p in later.significant_people] == ["Neha"]
    assert [p["name"] for p in later.facts["people"]] == ["നേഹ (Neha)"]

    # And the next session, built fresh, never sees her either.
    fresh = await orchestrator.build_prior_context(user_id, "childhood", real_db)
    assert [p["name"] for p in fresh.significant_people] == ["Neha"]
    assert [p["name"] for p in fresh.facts["people"]] == ["നേഹ (Neha)"]


async def test_set_aside_people_and_topics_leave_layer_3(real_db):
    """The live case: Rani came back four times after the parent said she
    barely remembered her and that Nagpur meant little to her."""
    user_id = f"freeze-{uuid.uuid4().hex[:8]}"
    session_id = await _user_and_session(real_db, user_id)
    await fact_store.update_facts(
        user_id,
        {
            "people": [
                {"name": "റാണി (Rani)", "relationship": "childhood friend in Nagpur"},
                {"name": "ഉമ (Uma)", "relationship": "cousin, like a sister"},
            ]
        },
        real_db,
    )
    first = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )
    assert len(first.facts["people"]) == 2

    await fact_store.add_set_aside(
        user_id,
        [
            {"kind": "person", "name": "Rani"},
            {"kind": "topic", "name": "childhood years in Nagpur"},
        ],
        real_db,
    )

    # Same session, frozen snapshot: Rani goes now, not tomorrow.
    later = await orchestrator.prior_context_for_session(
        session_id, user_id, "childhood", real_db
    )
    assert [p["name"] for p in later.facts["people"]] == ["ഉമ (Uma)"]
    assert later.set_aside == ["Rani", "childhood years in Nagpur"]
    assert "set_aside" not in later.facts

    fresh = await orchestrator.build_prior_context(user_id, "childhood", real_db)
    assert [p["name"] for p in fresh.facts["people"]] == ["ഉമ (Uma)"]
    assert fresh.set_aside == ["Rani", "childhood years in Nagpur"]


async def test_the_last_goodbye_comes_from_the_previous_session(real_db):
    from models.turn import Turn

    user_id = f"freeze-{uuid.uuid4().hex[:8]}"
    old_session = await _user_and_session(real_db, user_id)
    for n, text in (
        (1, "[parent consent: yes]"),
        (2, "Tomorrow, let's start with the big event you mentioned."),
    ):
        real_db.add(
            Turn(
                session_id=uuid.UUID(old_session),
                user_id=user_id,
                turn_number=n,
                transcript="...",
                response_text=text,
            )
        )
    await real_db.commit()
    new_session = uuid.uuid4()
    real_db.add(
        Session(
            id=new_session,
            user_id=user_id,
            session_number=5,
            domain="family_ancestors",
            status="active",
        )
    )
    real_db.add(
        Turn(
            session_id=new_session,
            user_id=user_id,
            turn_number=1,
            transcript="hello",
            response_text="Good evening! Shall we talk about your grandparents?",
        )
    )
    await real_db.commit()

    context = await orchestrator.build_prior_context(
        user_id, "family_ancestors", real_db, str(new_session)
    )
    assert context.last_goodbye == (
        "Tomorrow, let's start with the big event you mentioned."
    )


async def test_previous_reply_is_the_one_before_this_turn(real_db):
    from models.turn import Turn

    user_id = f"freeze-{uuid.uuid4().hex[:8]}"
    session_id = await _user_and_session(real_db, user_id)
    turns = []
    for n, reply in (
        (1, "Where did you grow up?"),
        (2, "What made Divya so dear?"),
        (3, "Sorry."),
    ):
        t = Turn(
            session_id=uuid.UUID(session_id),
            user_id=user_id,
            turn_number=n,
            transcript="...",
            response_text=reply,
        )
        real_db.add(t)
        await real_db.commit()
        turns.append(t)

    assert await orchestrator.previous_reply(session_id, turns[2].id, real_db) == (
        "What made Divya so dear?"
    )
    assert await orchestrator.previous_reply(session_id, turns[0].id, real_db) == ""
    assert await orchestrator.previous_reply("not-a-uuid", turns[0].id, real_db) == ""
