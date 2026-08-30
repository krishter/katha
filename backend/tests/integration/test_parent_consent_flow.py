"""
S3.2/S3.3 — the parent consent conversation, end to end against real
Postgres.

The unit tests cover interpretation and wording in isolation. This covers
the thing that actually has to work: an inbound WhatsApp message from a
parent who has never agreed produces the welcome rather than a domain
session, her answer is read and recorded against her own principal with
the turn that carries it, and nothing advances until she says yes.
"""

from __future__ import annotations

import uuid
from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from core import parent_consent
from core.parent_consent import ConsentStatus
from main import app
from models.consent_record import ConsentRecord
from models.db import AsyncSessionLocal, get_db
from models.session import Session
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

_NUMBER = "+919000000777"


async def _seed_parent(db, user_id: str) -> UserProfileModel:
    profile = UserProfileModel(
        user_id=user_id,
        name="Lakshmi",
        whatsapp_number=_NUMBER,
        preferred_language="ta-IN",
        onboarding_context="",
        family_whatsapp_number="+919000000888",
        scheduled_time=time(10, 30),
        timezone="Asia/Kolkata",
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


def _stub_adapter():
    """A WhatsApp adapter that records what was said without sending."""
    adapter = MagicMock()
    adapter.validate_signature = MagicMock(return_value=True)
    adapter.send_voice_note = AsyncMock(return_value=("SM_V", "audio/x.ogg"))
    adapter.send_text = AsyncMock(return_value="SM_T")
    adapter.download_voice_note = AsyncMock(return_value=b"audio-bytes")
    return adapter


async def _post_inbound(client, body: str = "", with_audio: bool = False) -> None:
    data = {
        "From": f"whatsapp:{_NUMBER}",
        "MessageSid": f"SM{uuid.uuid4().hex[:12]}",
        "Body": body,
    }
    if with_audio:
        data["MediaUrl0"] = "https://example.test/audio.ogg"
        data["MediaContentType0"] = "audio/ogg"
    resp = await client.post("/webhook/whatsapp", data=data)
    assert resp.status_code == 200


def _spoken(adapter) -> str:
    """Everything Katha said, however it was delivered."""
    said = []
    for call in adapter.send_text.await_args_list:
        said.append(str(call.args[1]) if len(call.args) > 1 else "")
    return " ".join(said)


class _Harness:
    """Drives the real webhook with a per-request session, patching only
    the network edges — Twilio, Sarvam STT and Sarvam TTS."""

    def __init__(self, transcript: str = ""):
        self.adapter = _stub_adapter()
        self.transcript = transcript
        self.spoken: list[str] = []

    async def __aenter__(self):
        async def _override_get_db():
            async with AsyncSessionLocal() as s:
                yield s

        from adapters.whatsapp_stub import get_whatsapp_adapter

        self._prev = app.dependency_overrides.get(get_db)
        self._prev_wa = app.dependency_overrides.get(get_whatsapp_adapter)
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_whatsapp_adapter] = lambda: self.adapter

        async def _capture_tts(text, language_code=None):
            self.spoken.append(text)
            return b"wav"

        self._patches = [
            patch(
                "api.routes.webhook.sarvam_stt.transcribe",
                new=AsyncMock(
                    return_value=MagicMock(
                        transcript=self.transcript, language_code="ta-IN"
                    )
                ),
            ),
            patch("adapters.sarvam_tts.synthesize", new=_capture_tts),
            patch(
                "media.audio_convert.convert_wav_to_ogg",
                new=AsyncMock(return_value=b"ogg"),
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

        for key, prev in ((get_db, self._prev), (get_whatsapp_adapter, self._prev_wa)):
            if prev is not None:
                app.dependency_overrides[key] = prev
            else:
                app.dependency_overrides.pop(key, None)

    def everything_said(self) -> str:
        return " ".join(self.spoken) + " " + _spoken(self.adapter)


async def test_first_message_gets_the_welcome_not_a_domain_session(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")

    said = h.everything_said().lower()
    assert "not a person" in said, "the AI disclosure must be in the welcome"
    assert "would you be happy" in said, "the welcome must actually ask"

    # Nothing may advance into a conversation.
    real_db.expire_all()
    sessions = (
        (await real_db.execute(select(Session).where(Session.user_id == user_id)))
        .scalars()
        .all()
    )
    assert all(s.status == "consent" for s in sessions), (
        f"no domain session may open before consent: {[s.status for s in sessions]}"
    )

    state = await parent_consent.get_status(user_id, real_db)
    assert state.status is ConsentStatus.AWAITING


async def test_a_spoken_yes_is_recorded_with_the_turn_that_carries_it(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")  # welcome
    async with _Harness(transcript="Yes, that sounds lovely") as h2:
        await _post_inbound(h2.client, with_audio=True)  # her answer, spoken

    real_db.expire_all()
    records = (
        (
            await real_db.execute(
                select(ConsentRecord).where(ConsentRecord.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(records) == 1
    record = records[0]
    assert record.principal == "parent"
    assert record.evidence_ref is not None, (
        "a consent record pointing at nothing proves nothing"
    )

    state = await parent_consent.get_status(user_id, real_db)
    assert state.status is ConsentStatus.GRANTED


async def test_a_refusal_is_recorded_and_stops_everything(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")
    async with _Harness(transcript="No, I don't want this") as h2:
        await _post_inbound(h2.client, with_audio=True)

    real_db.expire_all()
    state = await parent_consent.get_status(user_id, real_db)
    assert state.status is ConsentStatus.DECLINED

    record = (
        (
            await real_db.execute(
                select(ConsentRecord).where(ConsentRecord.user_id == user_id)
            )
        )
        .scalars()
        .first()
    )
    assert record.consent_version.endswith("-declined")
    assert "not record anything" in h2.everything_said().lower()


async def test_ambiguity_reasks_once_then_halts(real_db):
    """Pestering someone who does not understand what is being asked is its
    own harm. One clarification, then a human looks at it."""
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")  # ask 1: welcome
    async with _Harness(transcript="hmm, who is this") as h2:
        await _post_inbound(h2.client, with_audio=True)  # ask 2: re-ask

    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.HALTED
    )

    # A third message must not produce a third ask.
    async with _Harness(transcript="what is happening") as h3:
        await _post_inbound(h3.client, with_audio=True)
    assert h3.everything_said().strip() == "", "halted means silent, not louder"

    records = (
        (
            await real_db.execute(
                select(ConsentRecord).where(ConsentRecord.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    assert records == [], "an unclear answer is not an answer"
