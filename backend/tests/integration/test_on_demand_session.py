"""
A parent who messages when no session is active gets a conversation.

The scheduled session is abandoned four hours after it opens, so for twenty
hours a day there was no active session and the webhook answered "Hi! Your
session isn't scheduled yet." Observed in production 2026-10-04: five
consecutive days of `session_number=1, childhood, abandoned, timeout`, and a
voice note at 15:14 UTC turned away because the window had closed at 08:01.

Compounding it, the 09:30 opener is rejected by Meta outside the 24-hour
window (63016), so she is never told a window existed. The product refused
to talk to the person it exists to listen to, at the moment she reached out.

These run the real webhook against real Postgres. Only the paid edges are
stubbed: Sarvam STT/TTS, Anthropic, and the Twilio send.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from core import parent_consent
from main import app
from models.consent_record import ConsentRecord
from models.db import AsyncSessionLocal, get_db
from models.session import Session
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

_NUMBER = "+919000000555"


async def _seed_consented_parent(db) -> str:
    """A parent who has agreed — past the consent gate, no active session."""
    user_id = f"od-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Lakshmi",
            whatsapp_number=_NUMBER,
            preferred_language="ta-IN",
            onboarding_context="",
            family_whatsapp_number="+919000000112",
            scheduled_time=time(9, 30),
            timezone="Asia/Kolkata",
        )
    )
    db.add(
        ConsentRecord(
            user_id=user_id,
            email_hash="on-demand-test",
            consent_version="1.1-parent",
            principal="parent",
            consented_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    assert await parent_consent.has_granted(user_id, db)
    return user_id


class _Harness:
    """Drives the real webhook with only the network edges patched."""

    def __init__(self):
        self.adapter = MagicMock()
        self.adapter.validate_signature = MagicMock(return_value=True)
        self.adapter.send_voice_note = AsyncMock(return_value=("SM_V", "audio/x.ogg"))
        self.adapter.send_text = AsyncMock(return_value="SM_T")
        self.adapter.download_voice_note = AsyncMock(return_value=b"audio")
        self.spoken: list[str] = []

    async def __aenter__(self):
        from adapters.whatsapp_stub import get_whatsapp_adapter

        async def _db():
            async with AsyncSessionLocal() as s:
                yield s

        self._prev_db = app.dependency_overrides.get(get_db)
        self._prev_wa = app.dependency_overrides.get(get_whatsapp_adapter)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_whatsapp_adapter] = lambda: self.adapter

        async def _tts(text, language_code=None):
            self.spoken.append(text)
            return b"RIFF" + b"\x00" * 40

        async def _llm(messages, system=None, max_tokens=500):
            content = (
                "<response>Tell me more about that.</response>"
                if system is not None
                else "<extraction>{}</extraction>"
            )
            return SimpleNamespace(content=content, input_tokens=10, output_tokens=5)

        self._patches = [
            patch(
                "core.orchestrator.sarvam_stt.transcribe",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        transcript="I grew up in Madurai.",
                        language_code="ta-IN",
                        language_probability=0.95,
                    )
                ),
            ),
            patch("adapters.llm.chat", new=_llm),
            patch("core.orchestrator.sarvam_tts.synthesize", new=_tts),
            patch("adapters.sarvam_tts.synthesize", new=_tts),
            patch(
                "core.orchestrator.convert_wav_to_ogg",
                new=AsyncMock(side_effect=lambda b: b),
            ),
            patch(
                "media.audio_convert.convert_wav_to_ogg",
                new=AsyncMock(side_effect=lambda b: b),
            ),
        ]
        for p in self._patches:
            p.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        return self

    async def __aexit__(self, *exc):
        for p in self._patches:
            p.stop()
        from adapters.whatsapp_stub import get_whatsapp_adapter

        for key, prev in (
            (get_db, self._prev_db),
            (get_whatsapp_adapter, self._prev_wa),
        ):
            if prev is not None:
                app.dependency_overrides[key] = prev
            else:
                app.dependency_overrides.pop(key, None)
        await self.client.aclose()

    async def inbound(self):
        return await self.client.post(
            "/webhook/whatsapp",
            data={
                "From": f"whatsapp:{_NUMBER}",
                "MessageSid": f"SM{uuid.uuid4().hex[:12]}",
                "MediaUrl0": "https://example.test/a.ogg",
                "MediaContentType0": "audio/ogg",
            },
        )

    def everything_said(self) -> str:
        texts = [
            str(c.args[1]) if len(c.args) > 1 else ""
            for c in self.adapter.send_text.await_args_list
        ]
        return " ".join(self.spoken + texts).lower()


async def _sessions(db, user_id) -> list[Session]:
    db.expire_all()
    return list(
        (await db.execute(select(Session).where(Session.user_id == user_id)))
        .scalars()
        .all()
    )


async def test_a_voice_note_with_no_active_session_opens_one(real_db):
    """The regression: she reaches out, Katha listens."""
    user_id = await _seed_consented_parent(real_db)

    async with _Harness() as h:
        response = await h.inbound()
        assert response.status_code == 200
        said = h.everything_said()

    assert "isn't scheduled" not in said and "not scheduled" not in said, (
        f"she was turned away: {said[:160]!r}"
    )

    rows = await _sessions(real_db, user_id)
    domain_sessions = [s for s in rows if s.status != "consent"]
    assert len(domain_sessions) == 1, (
        f"expected one session opened on demand, got {[s.status for s in rows]}"
    )
    assert domain_sessions[0].status == "active"


async def test_an_abandoned_session_does_not_block_a_new_one(real_db):
    """
    Exactly the production shape: this morning's session timed out
    unanswered, and she messages in the evening. An abandoned session means
    she never got to speak, so it must not be a reason to refuse her.
    """
    user_id = await _seed_consented_parent(real_db)
    real_db.add(
        Session(
            id=uuid.uuid4(),
            user_id=user_id,
            session_number=1,
            domain="childhood",
            status="abandoned",
            started_at=datetime.now(timezone.utc) - timedelta(hours=11),
            ended_at=datetime.now(timezone.utc) - timedelta(hours=7),
            ended_reason="timeout",
        )
    )
    await real_db.commit()

    async with _Harness() as h:
        await h.inbound()
        said = h.everything_said()

    assert "already had our conversation" not in said
    active = [s for s in await _sessions(real_db, user_id) if s.status == "active"]
    assert len(active) == 1, "an abandoned session wrongly blocked a new one"


async def test_a_completed_session_today_is_not_repeated(real_db):
    """One conversation a day. She is answered warmly, not stonewalled."""
    user_id = await _seed_consented_parent(real_db)
    real_db.add(
        Session(
            id=uuid.uuid4(),
            user_id=user_id,
            session_number=1,
            domain="childhood",
            status="completed",
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
            ended_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ended_reason="goal_met",
        )
    )
    await real_db.commit()

    async with _Harness() as h:
        await h.inbound()
        said = h.everything_said()

    assert "already had our conversation" in said, f"got: {said[:160]!r}"
    assert "isn't scheduled" not in said, "the cold brush-off survived"

    active = [s for s in await _sessions(real_db, user_id) if s.status == "active"]
    assert not active, "a second session was opened on the same day"


async def test_an_active_session_is_reused_not_duplicated(real_db):
    """Two voice notes in a row must be one conversation, not two."""
    user_id = await _seed_consented_parent(real_db)

    async with _Harness() as h:
        await h.inbound()
        await h.inbound()

    domain_sessions = [
        s for s in await _sessions(real_db, user_id) if s.status != "consent"
    ]
    assert len(domain_sessions) == 1, (
        f"two messages produced {len(domain_sessions)} sessions"
    )
