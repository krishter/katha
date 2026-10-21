"""
P4 (M-02, D4): she can stop, against real Postgres, through the real
webhook.

The withdrawal classifier's model replies are replayed from
tests/fixtures/withdraw_classifier_replay.json, recorded by
`run_eval.py withdraw` with EVAL_RECORD_WITHDRAW=1, so the fixed phrases,
the prefilter, the classifier's parsing and its quoted-span rule all run
for real. A transcript with no recorded reply fails the test.

Email is captured at core.email's SES call: the operator alert and the
family's email both go through it.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import func, select

from core import erasure, parent_consent, session_manager, withdrawal
from core.parent_consent import ConsentStatus
from main import app
from models.consent_record import ConsentRecord
from models.db import AsyncSessionLocal, get_db
from models.erasure_log import ErasureLog
from models.family_account import FamilyAccount
from models.session import Session
from models.turn import Turn
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

_REPLAY = json.loads(
    (
        pathlib.Path(__file__).resolve().parent.parent
        / "fixtures/withdraw_classifier_replay.json"
    ).read_text()
)["replies"]

_STOP = "Bas, band karo, mujhe nahi chahiye"
_STORY = "My father told us to stop playing and come home"


def _number() -> str:
    return f"+9197{uuid.uuid4().int % 100000000:08d}"


async def _agreed_parent(db, *, language="hi-IN") -> SimpleNamespace:
    """Plain values, not the ORM row: the tests expire the session, and an
    expired row's attributes cannot be read outside a greenlet."""
    user_id = f"wd-{uuid.uuid4().hex[:8]}"
    number = _number()
    profile = UserProfileModel(
        user_id=user_id,
        name="Kamala",
        whatsapp_number=number,
        family_whatsapp_number="+919000000300",
        preferred_language=language,
        onboarding_context="",
        scheduled_time=time(9, 30),
    )
    db.add(profile)
    db.add(
        FamilyAccount(
            email=f"{user_id}@example.test",
            user_id=user_id,
            plan="free",
            onboarding_complete=True,
        )
    )
    db.add(
        ConsentRecord(
            user_id=user_id,
            email_hash="",
            consent_version=parent_consent.PARENT_CONSENT_VERSION,
            principal=parent_consent.PRINCIPAL_PARENT,
            whatsapp_number=number,
        )
    )
    await db.commit()
    return SimpleNamespace(user_id=user_id, whatsapp_number=number)


class _Webhook:
    """The real webhook with Twilio, Sarvam and SES stubbed at the edge."""

    def __init__(self, transcript: str = ""):
        self.transcript = transcript
        self.adapter = MagicMock()
        self.adapter.validate_signature = MagicMock(return_value=True)
        self.adapter.send_voice_note = AsyncMock(return_value=("SM_V", "a.ogg"))
        self.adapter.send_text = AsyncMock(return_value="SM_T")
        self.adapter.download_voice_note = AsyncMock(return_value=b"audio")
        self.spoken: list[str] = []
        self.emails: list[tuple[str, str]] = []
        self.unrecorded: list[str] = []
        self.stt = AsyncMock(
            return_value=SimpleNamespace(
                transcript=transcript, language_code="hi-IN", language_probability=1
            )
        )

    async def __aenter__(self):
        from adapters.whatsapp_stub import get_whatsapp_adapter

        async def _db():
            async with AsyncSessionLocal() as s:
                yield s

        async def _tts(text, language_code=None, **_kwargs):
            self.spoken.append(text)
            return b"wav"

        async def _email(to, subject, body_text, body_html):
            self.emails.append((to, subject))

        async def _classifier(messages, **_kwargs):
            reply = (
                messages[0]
                .content.split("HER REPLY\n", 1)[1]
                .split("\n\nRespond with", 1)[0]
            )
            if reply not in _REPLAY:
                self.unrecorded.append(reply)
                raise KeyError(reply)
            return SimpleNamespace(
                content=_REPLAY[reply],
                input_tokens=1,
                output_tokens=1,
                stop_reason="end_turn",
            )

        self._prev = {
            dep: app.dependency_overrides.get(dep)
            for dep in (get_db, get_whatsapp_adapter)
        }
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_whatsapp_adapter] = lambda: self.adapter
        self._patches = [
            patch("api.routes.webhook.sarvam_stt.transcribe", new=self.stt),
            patch("adapters.llm.chat", new=_classifier),
            patch("adapters.sarvam_tts.synthesize", new=_tts),
            patch("adapters.gemini_tts.synthesize", new=_tts),
            patch(
                "media.audio_convert.convert_wav_to_ogg",
                new=AsyncMock(return_value=b"ogg"),
            ),
            patch("core.email.send_email_ses", new=_email),
            patch("core.withdrawal.send_email_ses", new=_email),
            patch("config.settings.OPERATOR_EMAIL", "ops@example.test"),
        ]
        for p in self._patches:
            p.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()
        for p in reversed(self._patches):
            p.stop()
        for dep, prev in self._prev.items():
            if prev is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = prev
        assert not self.unrecorded, f"no recorded reply for {self.unrecorded}"

    async def voice_note(self, number: str) -> None:
        response = await self.client.post(
            "/webhook/whatsapp",
            data={
                "From": f"whatsapp:{number}",
                "MessageSid": f"SM{uuid.uuid4().hex[:12]}",
                "MediaUrl0": "https://example.test/a.ogg",
                "MediaContentType0": "audio/ogg",
            },
        )
        assert response.status_code == 200

    def outbound(self) -> int:
        return (
            self.adapter.send_voice_note.await_count
            + self.adapter.send_text.await_count
        )


async def _status(db, user_id):
    db.expire_all()
    return (await parent_consent.get_status(user_id, db)).status


async def _profile(db, user_id) -> UserProfileModel:
    db.expire_all()
    return (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()


# ── a stop is heard, and does everything D4 says ─────────────────────────────


async def test_a_short_stop_mid_session_withdraws_her(real_db):
    profile = await _agreed_parent(real_db)
    state = await session_manager.start_session(profile.user_id, real_db)

    async with _Webhook(_STOP) as hook:
        await hook.voice_note(profile.whatsapp_number)

    assert await _status(real_db, profile.user_id) is ConsentStatus.WITHDRAWN

    session = (
        await real_db.execute(
            select(Session).where(Session.id == uuid.UUID(state.session_id))
        )
    ).scalar_one()
    assert session.status == "completed"
    assert session.ended_reason == "withdrawn"

    record = (
        await real_db.execute(
            select(ConsentRecord)
            .where(ConsentRecord.user_id == profile.user_id)
            .where(ConsentRecord.consent_version == parent_consent.WITHDRAWN_VERSION)
        )
    ).scalar_one()
    turn = (
        await real_db.execute(select(Turn).where(Turn.id == record.evidence_ref))
    ).scalar_one()
    assert turn.transcript == _STOP, "her words are the evidence"
    assert record.whatsapp_number == profile.whatsapp_number

    assert hook.spoken == [parent_consent.withdraw_text("hi-IN")], "once, in Hindi"
    subjects = [subject for _, subject in hook.emails]
    assert any("withdrew consent" in s for s in subjects), "operator alerted"
    assert any(to == f"{profile.user_id}@example.test" for to, _ in hook.emails), (
        "the family is told"
    )

    after = await _profile(real_db, profile.user_id)
    hold = after.erase_after - datetime.now(timezone.utc)
    assert timedelta(days=6, hours=23) < hold <= timedelta(days=7)

    # And then she is not answered.
    async with _Webhook(_STOP) as again:
        await again.voice_note(profile.whatsapp_number)
    assert again.outbound() == 0


async def test_a_stop_after_the_days_session_is_still_heard(real_db):
    """The session closed an hour ago. Her "stop" must not meet "we already
    talked today"."""
    profile = await _agreed_parent(real_db)
    real_db.add(
        Session(
            id=uuid.uuid4(),
            user_id=profile.user_id,
            session_number=1,
            domain="childhood",
            exchange_count=4,
            status="completed",
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ended_at=datetime.now(timezone.utc),
        )
    )
    await real_db.commit()

    async with _Webhook(_STOP) as hook:
        await hook.voice_note(profile.whatsapp_number)

    assert await _status(real_db, profile.user_id) is ConsentStatus.WITHDRAWN
    assert hook.spoken == [parent_consent.withdraw_text("hi-IN")]


async def test_a_story_with_stop_in_it_is_a_story(real_db):
    """Prefilter, then the classifier, on the real path: the turn goes on,
    with the transcript the webhook already has (no second STT)."""
    profile = await _agreed_parent(real_db, language="en-IN")
    await session_manager.start_session(profile.user_id, real_db)

    turn = AsyncMock(return_value=SimpleNamespace(turn_id=None, crisis_detected=False))
    async with _Webhook(_STORY) as hook:
        with (
            patch("api.routes.webhook.orchestrator.process_voice_turn", new=turn),
            patch("api.routes.webhook._deliver_turn_result", new=AsyncMock()),
        ):
            await hook.voice_note(profile.whatsapp_number)

    assert await _status(real_db, profile.user_id) is ConsentStatus.GRANTED
    turn.assert_awaited_once()
    assert turn.await_args.kwargs["stt_result"].transcript == _STORY
    assert hook.stt.await_count == 1, "transcribed once"


async def test_a_crisis_is_never_read_as_a_withdrawal():
    assert not await withdrawal.is_withdrawal("I want to die, stop recording")


# ── after a no, quiet ────────────────────────────────────────────────────────


async def test_after_a_decline_three_messages_get_no_more_than_one_reply(real_db):
    user_id = f"wd-{uuid.uuid4().hex[:8]}"
    number = _number()
    real_db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=number,
            preferred_language="hi-IN",
            onboarding_context="",
            scheduled_time=time(9, 30),
        )
    )
    await real_db.commit()

    async with _Webhook("Namaste") as hook:
        await hook.voice_note(number)  # the welcome
    async with _Webhook("Nahi") as hook:
        await hook.voice_note(number)  # declined, and told so once
    assert await _status(real_db, user_id) is ConsentStatus.DECLINED

    async with _Webhook("Hello?") as hook:
        for _ in range(3):
            await hook.voice_note(number)
    assert hook.outbound() <= 1
    assert hook.outbound() == 0, "she was told on declining; nothing more"


async def test_a_month_later_she_is_answered_once(real_db):
    profile = await _agreed_parent(real_db)
    async with _Webhook(_STOP) as hook:
        await hook.voice_note(profile.whatsapp_number)
    row = await _profile(real_db, profile.user_id)
    row.last_decline_reply_at = datetime.now(timezone.utc) - timedelta(days=31)
    await real_db.commit()

    async with _Webhook("Hello?") as hook:
        await hook.voice_note(profile.whatsapp_number)
        await hook.voice_note(profile.whatsapp_number)
    # The words come from the system-speech cache by now, so count sends.
    assert hook.adapter.send_voice_note.await_count == 1


# ── erasure after the hold ───────────────────────────────────────────────────


async def test_the_hold_passing_erases_everything_but_the_audit_trail(real_db):
    profile = await _agreed_parent(real_db)
    await session_manager.start_session(profile.user_id, real_db)
    async with _Webhook(_STOP) as hook:
        await hook.voice_note(profile.whatsapp_number)
    erase_after = (await _profile(real_db, profile.user_id)).erase_after

    with patch("core.erasure.storage.delete_media", new=AsyncMock()):
        async with AsyncSessionLocal() as db:
            assert await withdrawal.erase_due(db, now=erase_after - timedelta(1)) == 0
        async with AsyncSessionLocal() as db:
            erased = await withdrawal.erase_due(db, now=erase_after + timedelta(1))
    assert erased == 1

    real_db.expire_all()
    for model in (UserProfileModel, Session, Turn, FamilyAccount):
        left = (
            await real_db.execute(
                select(func.count())
                .select_from(model)
                .where(model.user_id == profile.user_id)
            )
        ).scalar_one()
        assert left == 0, model.__tablename__
    assert (
        await real_db.execute(
            select(func.count())
            .select_from(ConsentRecord)
            .where(ConsentRecord.user_id == profile.user_id)
        )
    ).scalar_one() == 0, "consent records are anonymised, not kept under her id"

    log = (
        await real_db.execute(
            select(ErasureLog).where(
                ErasureLog.user_id_hash == erasure.hash_user_id(profile.user_id)
            )
        )
    ).scalar_one()
    assert log.actor == erasure.ACTOR_HOLD
    assert log.failures == []


async def test_a_cleared_hold_is_never_due(real_db):
    from scripts import erase_user

    profile = await _agreed_parent(real_db)
    async with _Webhook(_STOP) as hook:
        await hook.voice_note(profile.whatsapp_number)

    async with AsyncSessionLocal() as db:
        outcome = await erase_user.run(profile.user_id, "test", db, cancel_hold=True)
    assert outcome.startswith("CLEARED")

    async with AsyncSessionLocal() as db:
        far_future = datetime.now(timezone.utc) + timedelta(days=365)
        assert await withdrawal.erase_due(db, now=far_future) == 0
    assert (await _profile(real_db, profile.user_id)) is not None
    assert await _status(real_db, profile.user_id) is ConsentStatus.WITHDRAWN
