"""
S2.2 — the deletion flow, end to end against real Postgres.

There is no browser test harness in this project, so "E2E" here means the
full API path the portal actually calls, in order, against a real database:
seed a complete user, export, delete, then verify by direct table
inspection rather than by trusting the endpoint's response — which reports
success cheerfully whether or not anything was removed.

The unit tests in tests/test_data_deletion.py assert the *sequence* of
statements against a mocked session. They cannot catch a WHERE clause that
matches nothing, a cascade that does not fire, or a commit that never
lands. This can.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timezone

import httpx
import pytest
from sqlalchemy import select

from core.auth import get_current_user
from main import app
from models.api_usage import ApiUsage
from models.consent_record import ConsentRecord
from models.db import AsyncSessionLocal, get_db
from models.fact import Fact
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel
from models.voice_change import VoiceChange

pytestmark = pytest.mark.integration


async def _seed_full_user(
    db, user_id: str, number: str = "+919000000111", email_hash: str = "e2e-hash"
) -> uuid.UUID:
    """A user with something in every table deletion is meant to reach."""
    session_id = uuid.uuid4()

    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Subramaniam",
            whatsapp_number=number,
            preferred_language="en-IN",
            onboarding_context="Grew up in Madurai.",
            family_whatsapp_number="+919000000112",
            scheduled_time=time(10, 30),
            timezone="Asia/Kolkata",
        )
    )
    db.add(
        Session(
            id=session_id,
            user_id=user_id,
            session_number=1,
            domain="childhood",
            exchange_count=2,
            status="completed",
            session_open_message_id="STUB_OPEN",
            session_open_audio_s3_key=f"audio/{user_id}-open.ogg",
        )
    )
    await db.commit()

    turn = Turn(
        session_id=session_id,
        user_id=user_id,
        turn_number=1,
        transcript="I was born in Madurai in 1948.",
        detected_language="en-IN",
        response_text="Tell me about the street.",
        extraction_json={},
        response_audio_s3_key=f"audio/{user_id}-turn1.ogg",
        extraction_status="done",
    )
    db.add(turn)
    await db.commit()
    await db.refresh(turn)

    atom = StoryAtom(
        session_id=session_id,
        turn_id=turn.id,
        user_id=user_id,
        domain="childhood",
        title="The street",
        narrative="A provisions shop on the street outside.",
        who=["father"],
        completeness_score=3,
        verbatim_quote="It smelled of jasmine.",
        open_threads=["what did the shop sell"],
    )
    db.add(atom)
    await db.commit()
    await db.refresh(atom)

    db.add(
        MemoryCard(
            session_id=session_id,
            user_id=user_id,
            story_atom_id=atom.id,
            verbatim_quote="It smelled of jasmine.",
            domain="childhood",
            image_s3_key=f"cards/{session_id}.png",
        )
    )
    db.add(
        Fact(
            user_id=user_id,
            structured_facts={"places": ["Madurai"]},
            significant_people=[],
        )
    )
    db.add(
        ApiUsage(
            user_id=user_id,
            session_id=session_id,
            turn_id=turn.id,
            vendor="anthropic",
            operation="dialogue",
            model="claude-sonnet-4-6",
            units={"input_tokens": 1200, "output_tokens": 80},
        )
    )
    db.add(
        VoiceChange(
            user_id=user_id,
            from_voice="sarvam/bulbul:v4-flash/simran_en_conversation",
            to_voice="google/gemini-3.8-flash-lite-tts/Sulafat",
            changed_by="Krish",
            reason="D2 switch",
        )
    )
    db.add(
        ConsentRecord(
            user_id=user_id,
            email_hash=email_hash,
            consent_version="1.1",
            consented_at=datetime.now(timezone.utc),
            ip_address="203.0.113.9",
            user_agent="e2e/1.0",
        )
    )
    await db.commit()
    return session_id


async def _count_rows(db, user_id: str) -> dict:
    counts = {}
    for label, model in [
        ("user_profiles", UserProfileModel),
        ("sessions", Session),
        ("turns", Turn),
        ("story_atoms", StoryAtom),
        ("memory_cards", MemoryCard),
        ("facts", Fact),
        ("api_usage", ApiUsage),
        ("voice_changes", VoiceChange),
    ]:
        rows = (
            (await db.execute(select(model).where(model.user_id == user_id)))
            .scalars()
            .all()
        )
        counts[label] = len(rows)
    return counts


async def test_export_then_delete_removes_every_row(real_db, monkeypatch):
    user_id = f"e2e-{uuid.uuid4().hex[:8]}"
    await _seed_full_user(real_db, user_id)

    # S3 objects are not exercised here — the bucket is audited separately
    # by the S1.2 consent-audit script. Stub the deletes so this test is
    # about database state and needs no network.
    async def _fake_delete_media(key: str) -> None:
        return None

    monkeypatch.setattr("core.erasure.storage.delete_media", _fake_delete_media)
    monkeypatch.setattr(
        "api.routes.family.storage.generate_presigned_url",
        lambda key, **kw: _noop_url(),
    )

    # A fresh session per request. Handing the app the test's own session
    # drives one asyncpg connection from two places at once ("another
    # operation is in progress") — the app's request and the test's asserts
    # are concurrent users of it.
    async def _override_get_db():
        async with AsyncSessionLocal() as request_session:
            yield request_session

    # Save and restore rather than pop: test_webhook.py and
    # test_conversation.py register their own module-level get_db override
    # on this same shared `app`, and popping would delete theirs for the
    # rest of the session.
    prev_db = app.dependency_overrides.get(get_db)
    prev_user = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "e2e@katha.life",
        "user_id": user_id,
    }
    transport = httpx.ASGITransport(app=app)
    try:
        client = httpx.AsyncClient(transport=transport, base_url="http://test")

        before = await _count_rows(real_db, user_id)
        assert all(v > 0 for v in before.values()), before

        # 1. The export the UI offers immediately before deletion.
        export = await client.get("/family/export")
        assert export.status_code == 200
        bundle = export.json()
        assert len(bundle["stories"]) == 1
        assert bundle["stories"][0]["verbatim_quote"] == "It smelled of jasmine."
        assert bundle["audio_included"] is False

        # 2. Deletion.
        deleted = await client.request("DELETE", f"/user/{user_id}")
        assert deleted.status_code == 200

        # 3. Verify by inspection, not by the response body.
        real_db.expire_all()
        after = await _count_rows(real_db, user_id)
        assert after == {
            "user_profiles": 0,
            "sessions": 0,
            "turns": 0,
            "story_atoms": 0,
            "memory_cards": 0,
            "facts": 0,
            "api_usage": 0,
            "voice_changes": 0,
        }, after

        # Consent records are retained and anonymised, never hard-deleted.
        consents = (
            (
                await real_db.execute(
                    select(ConsentRecord).where(ConsentRecord.email_hash == "e2e-hash")
                )
            )
            .scalars()
            .all()
        )
        assert len(consents) == 1
        assert consents[0].user_id == "DELETED"
        assert consents[0].ip_address is None
        assert consents[0].user_agent is None
        assert consents[0].consent_version == "1.1"
    finally:
        if prev_db is not None:
            app.dependency_overrides[get_db] = prev_db
        else:
            app.dependency_overrides.pop(get_db, None)
        if prev_user is not None:
            app.dependency_overrides[get_current_user] = prev_user
        else:
            app.dependency_overrides.pop(get_current_user, None)
        await real_db.execute(
            ConsentRecord.__table__.delete().where(
                ConsentRecord.email_hash == "e2e-hash"
            )
        )
        await real_db.commit()


async def test_delete_rejects_another_users_id(real_db, monkeypatch):
    """The path parameter is validated against the JWT. Without this, the
    settings page would be a one-field form for deleting other families."""
    user_id = f"e2e-{uuid.uuid4().hex[:8]}"
    await _seed_full_user(real_db, user_id)

    async def _override_get_db():
        async with AsyncSessionLocal() as request_session:
            yield request_session

    prev_db = app.dependency_overrides.get(get_db)
    prev_user = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "attacker@example.com",
        "user_id": "somebody-else",
    }
    transport = httpx.ASGITransport(app=app)
    try:
        client = httpx.AsyncClient(transport=transport, base_url="http://test")
        response = await client.request("DELETE", f"/user/{user_id}")
        assert response.status_code == 403

        real_db.expire_all()
        after = await _count_rows(real_db, user_id)
        assert all(v > 0 for v in after.values()), after
    finally:
        if prev_db is not None:
            app.dependency_overrides[get_db] = prev_db
        else:
            app.dependency_overrides.pop(get_db, None)
        if prev_user is not None:
            app.dependency_overrides[get_current_user] = prev_user
        else:
            app.dependency_overrides.pop(get_current_user, None)
        await real_db.execute(
            ConsentRecord.__table__.delete().where(
                ConsentRecord.email_hash == "e2e-hash"
            )
        )
        await real_db.commit()


def _noop_url():
    return "https://signed.example/x.png"


async def test_the_operator_script_and_the_endpoint_leave_the_same_state(
    real_db, monkeypatch
):
    """P4: one sweep, three callers. The family's delete button and the
    operator's script must leave identical post-states, checked by table
    inspection, and each must leave an erasure_log row naming no one."""
    from core import erasure
    from models.erasure_log import ErasureLog
    from scripts import erase_user

    monkeypatch.setattr("core.erasure.storage.delete_media", _fake_delete)
    by_family = f"e2e-{uuid.uuid4().hex[:8]}"
    by_operator = f"e2e-{uuid.uuid4().hex[:8]}"
    await _seed_full_user(real_db, by_family, "+919000000121", "e2e-hash-family")
    await _seed_full_user(real_db, by_operator, "+919000000122", "e2e-hash-operator")

    async def _override_get_db():
        async with AsyncSessionLocal() as request_session:
            yield request_session

    prev_db = app.dependency_overrides.get(get_db)
    prev_user = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "e2e@katha.life",
        "user_id": by_family,
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            deleted = await client.request("DELETE", f"/user/{by_family}")
        assert deleted.status_code == 200

        async with AsyncSessionLocal() as operator_db:
            dry = await erase_user.run(by_operator, "test", operator_db)
            assert dry.startswith("DRY RUN")
            outcome = await erase_user.run(by_operator, "test", operator_db, apply=True)
        assert outcome.startswith("ERASED"), outcome

        real_db.expire_all()
        family_after = await _count_rows(real_db, by_family)
        operator_after = await _count_rows(real_db, by_operator)
        assert family_after == operator_after
        assert all(count == 0 for count in family_after.values()), family_after

        for email_hash in ("e2e-hash-family", "e2e-hash-operator"):
            consent = (
                await real_db.execute(
                    select(ConsentRecord).where(ConsentRecord.email_hash == email_hash)
                )
            ).scalar_one()
            assert consent.user_id == "DELETED"
            assert consent.ip_address is None and consent.user_agent is None
            assert consent.whatsapp_number is None

        logs = {
            row.actor: row
            for row in (
                await real_db.execute(
                    select(ErasureLog).where(
                        ErasureLog.user_id_hash.in_(
                            [
                                erasure.hash_user_id(by_family),
                                erasure.hash_user_id(by_operator),
                            ]
                        )
                    )
                )
            )
            .scalars()
            .all()
        }
        assert set(logs) == {"family", "operator:test"}
        assert logs["family"].counts == logs["operator:test"].counts
        assert logs["family"].failures == [] == logs["operator:test"].failures
        # The log names no one.
        assert by_family not in str(logs["family"].counts)
    finally:
        for key, prev in ((get_db, prev_db), (get_current_user, prev_user)):
            if prev is not None:
                app.dependency_overrides[key] = prev
            else:
                app.dependency_overrides.pop(key, None)
        await real_db.execute(
            ConsentRecord.__table__.delete().where(
                ConsentRecord.email_hash.in_(["e2e-hash-family", "e2e-hash-operator"])
            )
        )
        await real_db.commit()


async def _fake_delete(key: str) -> None:
    return None
