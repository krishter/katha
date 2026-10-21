"""
P9 (M-10, M-11): a crisis is answered in her language and reaches the
operator at once; failures and stopped backups reach the operator too,
without flooding them. Real Postgres; email captured at core.email's SES
call, which every operator alert goes through.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from core import alerts, orchestrator
from core.fallback_audio import FailureStage
from core.session_manager import SessionState
from main import app
from models.consent_record import ConsentRecord
from models.crisis_event import CrisisEvent
from models.db import AsyncSessionLocal, get_db
from models.session import Session
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.system_prompt import UserProfile
from speech import catalogue

pytestmark = [pytest.mark.integration, pytest.mark.real_alerts]

_TAMIL_CRISIS = "எனக்கு வாழ விரும்பவில்லை, எல்லாம் போதும்"


@pytest.fixture
def emails(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def _email(to, subject, body_text, body_html):
        sent.append((subject, body_text))

    monkeypatch.setattr("core.email.send_email_ses", _email)
    monkeypatch.setattr("config.settings.OPERATOR_EMAIL", "ops@example.test")
    return sent


async def _parent_in_session(db, language="ta-IN") -> SimpleNamespace:
    user_id = f"cr-{uuid.uuid4().hex[:8]}"
    number = f"+9193{uuid.uuid4().int % 100000000:08d}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Lakshmi",
            whatsapp_number=number,
            preferred_language=language,
            onboarding_context="",
            scheduled_time=time(9, 30),
        )
    )
    db.add(
        ConsentRecord(
            user_id=user_id,
            email_hash="",
            consent_version="parent-1.0",
            principal="parent",
            whatsapp_number=number,
        )
    )
    session_id = uuid.uuid4()
    db.add(
        Session(
            id=session_id,
            user_id=user_id,
            session_number=1,
            domain="childhood",
            exchange_count=1,
            status="active",
            started_at=datetime.now(timezone.utc),
            # How the webhook finds the active session.
            whatsapp_number=number,
        )
    )
    await db.commit()
    return SimpleNamespace(user_id=user_id, number=number, session_id=session_id)


async def test_a_tamil_crisis_is_answered_in_tamil_kept_private_and_alerted(
    real_db, emails
):
    parent = await _parent_in_session(real_db)
    spoken: list[str] = []

    async def _tts(text, language_code=None, **_kwargs):
        spoken.append(text)
        return b"wav"

    adapter = MagicMock()
    adapter.validate_signature = MagicMock(return_value=True)
    adapter.send_voice_note = AsyncMock(return_value=("SM_V", "audio/x.ogg"))
    adapter.send_text = AsyncMock(return_value="SM_T")
    adapter.download_voice_note = AsyncMock(return_value=b"audio")
    stt = AsyncMock(
        return_value=SimpleNamespace(
            transcript=_TAMIL_CRISIS, language_code="ta-IN", language_probability=1
        )
    )

    from adapters.whatsapp_stub import get_whatsapp_adapter

    async def _db():
        async with AsyncSessionLocal() as s:
            yield s

    prev = {d: app.dependency_overrides.get(d) for d in (get_db, get_whatsapp_adapter)}
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_whatsapp_adapter] = lambda: adapter
    try:
        with (
            patch("api.routes.webhook.sarvam_stt.transcribe", new=stt),
            patch("adapters.sarvam_tts.synthesize", new=_tts),
            patch("adapters.gemini_tts.synthesize", new=_tts),
            patch(
                "core.orchestrator.convert_wav_to_ogg",
                new=AsyncMock(return_value=b"ogg"),
            ),
            patch(
                "media.audio_convert.convert_wav_to_ogg",
                new=AsyncMock(return_value=b"ogg"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/webhook/whatsapp",
                    data={
                        "From": f"whatsapp:{parent.number}",
                        "MessageSid": f"SM{uuid.uuid4().hex[:12]}",
                        "MediaUrl0": "https://example.test/a.ogg",
                        "MediaContentType0": "audio/ogg",
                    },
                )
    finally:
        for dep, old in prev.items():
            if old is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = old
    assert response.status_code == 200

    # In Tamil, in her voice, with the helpline number as digits.
    tamil_reply = catalogue.text("crisis", "ta-IN")
    assert tamil_reply in spoken
    assert "9152987821" in tamil_reply

    real_db.expire_all()
    turn = (
        await real_db.execute(select(Turn).where(Turn.session_id == parent.session_id))
    ).scalar_one()
    assert turn.transcript == _TAMIL_CRISIS
    assert turn.is_private, "nothing from it is for a card or the dashboard"
    event = (
        await real_db.execute(
            select(CrisisEvent).where(CrisisEvent.session_id == parent.session_id)
        )
    ).scalar_one()
    assert event.turn_id == turn.id, "the event points at what she said"

    crisis_mails = [body for subject, body in emails if "Crisis" in subject]
    assert len(crisis_mails) == 1
    assert _TAMIL_CRISIS in crisis_mails[0], "the operator can read what she said"


async def test_four_failures_across_two_families_send_one_alert(real_db, emails):
    families = []
    for _ in range(2):
        parent = await _parent_in_session(real_db, language="en-IN")
        families.append(parent)
    profile = UserProfile(
        name="Lakshmi", preferred_language="en-IN", onboarding_context=""
    )

    for parent in families + families:
        state = SessionState(
            session_id=str(parent.session_id),
            user_id=parent.user_id,
            session_number=1,
            domain="childhood",
            exchange_count=1,
            energy_signal="high",
            goal_met=False,
            session_end_suggested=False,
        )
        await orchestrator._fallback_turn_result(
            FailureStage.LLM, state, "", "en-IN", user_profile=profile
        )

    failing = [
        subject for subject, _ in emails if subject.endswith("Turns are failing")
    ]
    assert len(failing) == 1, failing


async def test_a_stale_backup_is_reported_once_a_day(real_db, emails):
    now = datetime.now(timezone.utc)
    fresh = now - timedelta(hours=3)
    stale = now - timedelta(hours=30)

    assert await alerts.check_backup(now=now, newest=fresh)
    assert not [s for s, _ in emails if "Backups" in s]

    assert not await alerts.check_backup(now=now, newest=stale)
    assert not await alerts.check_backup(now=now, newest=stale)
    assert len([s for s, _ in emails if "Backups have stopped" in s]) == 1
