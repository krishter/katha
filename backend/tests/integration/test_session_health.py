"""scripts/session_health.py reports numbers, never what she said."""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone

import pytest

from models.session import Session
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel
from scripts import session_health

pytestmark = pytest.mark.integration

USER = "user_health_check"
SECRET_WORDS = "Our house by the Vellayani lake had a red door"


async def test_counts_only_and_layer3_dropout(real_db):
    db = real_db
    db.add(
        UserProfileModel(
            user_id=USER,
            name="Devaki",
            whatsapp_number="+919000000311",
            preferred_language="ml-IN",
            katha_gender="female",
            scheduled_time=time(10, 30),
        )
    )
    session = Session(
        id=uuid.uuid4(),
        user_id=USER,
        session_number=1,
        domain="childhood",
        exchange_count=2,
        status="ended",
        started_at=datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc),
    )
    db.add(session)
    await db.flush()
    for i, lang in enumerate(("ml-IN", "en-IN"), start=1):
        db.add(
            Turn(
                session_id=session.id,
                user_id=USER,
                turn_number=i,
                transcript=SECRET_WORDS,
                detected_language=lang,
                response_text="A reply Katha gave",
                extraction_json={},
                extraction_status="done",
            )
        )
    # One childhood atom, then a full Layer 3 window of newer career atoms:
    # childhood falls outside the window, so it has dropped out.
    domains = ["childhood"] + ["career"] * session_health.LAYER3_TOP_K
    for minute, domain in enumerate(domains):
        db.add(
            StoryAtom(
                session_id=session.id,
                user_id=USER,
                domain=domain,
                title="The red door",
                narrative=SECRET_WORDS,
                who=["Kunjamma"],
                open_threads=[],
                completeness_score=3,
                verbatim_quote=SECRET_WORDS,
                created_at=datetime(2026, 11, 20, 5, tzinfo=timezone.utc)
                + timedelta(minutes=minute),
            )
        )
    await db.commit()

    out = "\n".join(await session_health.report(USER, 10, db))

    assert "1/2" in out  # one turn of two heard in her own language
    assert "Dropped from Layer 3: childhood" in out
    for words in ("Vellayani", "red door", "Kunjamma", "A reply Katha gave", "Devaki"):
        assert words not in out


async def test_six_domains_inside_the_window_are_not_dropped(real_db):
    # The old check used a window of 5, so six domains looked like a dropout
    # even though Layer 3 still receives all of them.
    db = real_db
    user = "user_health_window"
    db.add(
        UserProfileModel(
            user_id=user,
            name="Saraswati",
            whatsapp_number="+919000000312",
            preferred_language="en-IN",
            katha_gender="female",
            scheduled_time=time(10, 30),
        )
    )
    session = Session(
        id=uuid.uuid4(),
        user_id=user,
        session_number=1,
        domain="childhood",
        exchange_count=0,
        status="ended",
        started_at=datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc),
    )
    db.add(session)
    await db.flush()
    for minute, domain in enumerate(
        (
            "childhood",
            "family_ancestors",
            "education",
            "career",
            "love_marriage",
            "hobbies",
        )
    ):
        db.add(
            StoryAtom(
                session_id=session.id,
                user_id=user,
                domain=domain,
                title="A title",
                narrative="A narrative",
                who=[],
                open_threads=[],
                completeness_score=3,
                created_at=datetime(2026, 11, 20, 5, minute, tzinfo=timezone.utc),
            )
        )
    await db.commit()

    out = "\n".join(await session_health.report(user, 10, db))

    assert f"newest {session_health.LAYER3_TOP_K} atoms" in out
    assert "covers 6 domain(s)" in out
    assert "Nothing dropped yet." in out
