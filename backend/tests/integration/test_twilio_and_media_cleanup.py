"""
P6 (M-05, M-08, D8): her messages leave Twilio, and every object Katha
uploads for her can be found and erased. Against real Postgres, with the
real deletion queue and object tracking (the markers below turn off the
in-memory capture other tests get), a recording WhatsApp adapter and an
in-memory bucket in place of S3.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from core import erasure, orchestrator, twilio_cleanup, usage
from main import app
from media import storage
from models.consent_record import ConsentRecord
from models.db import AsyncSessionLocal, get_db
from models.media_object import MediaObject
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.twilio_deletion import TwilioDeletion
from models.user_profile import UserProfileModel

pytestmark = [
    pytest.mark.integration,
    pytest.mark.real_twilio_queue,
    pytest.mark.real_media_tracking,
]


class _Bucket:
    """An in-memory S3 client: put, delete, and a listing to inspect."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.objects[Key] = Body

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)


@pytest.fixture
def bucket(monkeypatch):
    fake = _Bucket()
    monkeypatch.setattr("media.storage._s3_client", lambda: fake)
    return fake


def _adapter(fail_delete: Exception | None = None):
    adapter = MagicMock()
    adapter.validate_signature = MagicMock(return_value=True)
    adapter.send_voice_note = AsyncMock(return_value=("SM_V", "audio/x.ogg"))
    adapter.send_text = AsyncMock(return_value="SM_T")
    adapter.download_voice_note = AsyncMock(return_value=b"audio")
    adapter.delete_message = AsyncMock(side_effect=fail_delete)
    return adapter


async def _consented_parent(db) -> SimpleNamespace:
    user_id = f"tw-{uuid.uuid4().hex[:8]}"
    number = f"+9195{uuid.uuid4().int % 100000000:08d}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=number,
            preferred_language="en-IN",
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
    await db.commit()
    return SimpleNamespace(user_id=user_id, number=number)


async def _queued(db, sid) -> TwilioDeletion | None:
    db.expire_all()
    return (
        await db.execute(
            select(TwilioDeletion).where(TwilioDeletion.message_sid == sid)
        )
    ).scalar_one_or_none()


class _App:
    def __init__(self, adapter):
        self.adapter = adapter

    async def __aenter__(self):
        from adapters.whatsapp_stub import get_whatsapp_adapter

        async def _db():
            async with AsyncSessionLocal() as s:
                yield s

        self._prev = {
            dep: app.dependency_overrides.get(dep)
            for dep in (get_db, get_whatsapp_adapter)
        }
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_whatsapp_adapter] = lambda: self.adapter
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()
        for dep, prev in self._prev.items():
            if prev is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = prev


# ── inbound: deleted once heard ──────────────────────────────────────────────


async def test_her_voice_note_is_deleted_from_twilio_once_transcribed(real_db):
    parent = await _consented_parent(real_db)
    sid = f"MM{uuid.uuid4().hex[:12]}"
    adapter = _adapter()
    turn = AsyncMock(return_value=SimpleNamespace(turn_id=None, crisis_detected=False))

    async with _App(adapter) as running:
        with (
            patch(
                "api.routes.webhook.sarvam_stt.transcribe",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        transcript="We lived by the river.", language_code="en-IN"
                    )
                ),
            ),
            patch("api.routes.webhook.orchestrator.process_voice_turn", new=turn),
            patch("api.routes.webhook._deliver_turn_result", new=AsyncMock()),
        ):
            response = await running.client.post(
                "/webhook/whatsapp",
                data={
                    "From": f"whatsapp:{parent.number}",
                    "MessageSid": sid,
                    "MediaUrl0": f"https://api.twilio.com/Messages/{sid}/Media/ME1",
                    "MediaContentType0": "audio/ogg",
                },
            )
    assert response.status_code == 200

    adapter.delete_message.assert_awaited_once_with(sid)
    assert await _queued(real_db, sid) is None, "deleted, so no longer queued"


async def test_a_voice_note_that_was_not_heard_is_deleted_when_due(real_db):
    parent = await _consented_parent(real_db)
    sid = f"MM{uuid.uuid4().hex[:12]}"
    adapter = _adapter()

    async with _App(adapter) as running:
        with (
            patch(
                "api.routes.webhook.sarvam_stt.transcribe",
                new=AsyncMock(side_effect=RuntimeError("stt down")),
            ),
            patch(
                "api.routes.webhook.orchestrator.process_voice_turn",
                new=AsyncMock(side_effect=RuntimeError("stt down")),
            ),
        ):
            await running.client.post(
                "/webhook/whatsapp",
                data={
                    "From": f"whatsapp:{parent.number}",
                    "MessageSid": sid,
                    "MediaUrl0": "https://api.twilio.com/x",
                    "MediaContentType0": "audio/ogg",
                },
            )

    queued = await _queued(real_db, sid)
    assert queued is not None and queued.user_id == parent.user_id
    adapter.delete_message.assert_not_awaited()

    later = datetime.now(timezone.utc) + twilio_cleanup.INBOUND_GRACE + timedelta(1)
    assert await twilio_cleanup.run_due(now=later, whatsapp=adapter) >= 1
    adapter.delete_message.assert_awaited_with(sid)
    assert await _queued(real_db, sid) is None


# ── outbound: deleted at a final status ──────────────────────────────────────


async def test_katha_s_message_is_deleted_at_a_final_status_only(real_db):
    sid = f"SM{uuid.uuid4().hex[:12]}"
    await twilio_cleanup.queue(sid, direction=twilio_cleanup.OUTBOUND, user_id="u")
    adapter = _adapter()

    async with _App(adapter) as running:
        for status in ("sent", "delivered"):
            await running.client.post(
                "/webhook/twilio/status",
                data={"MessageSid": sid, "MessageStatus": status},
            )
            if status == "sent":
                adapter.delete_message.assert_not_awaited()

    adapter.delete_message.assert_awaited_once_with(sid)
    assert await _queued(real_db, sid) is None


async def test_a_failed_delete_stays_queued_and_is_retried(real_db):
    sid = f"SM{uuid.uuid4().hex[:12]}"
    await twilio_cleanup.queue(sid, direction=twilio_cleanup.OUTBOUND, user_id="u")

    assert not await twilio_cleanup.delete_now(sid, _adapter(RuntimeError("503")))
    queued = await _queued(real_db, sid)
    assert queued.attempts == 1 and "503" in queued.last_error
    assert queued.due_at > datetime.now(timezone.utc)

    # Already gone at Twilio is as good as deleted.
    already_gone = RuntimeError("not found")
    already_gone.status = 404
    assert await twilio_cleanup.delete_now(sid, _adapter(already_gone))
    assert await _queued(real_db, sid) is None


# ── every upload is tracked, and erasure finds it ────────────────────────────


async def test_an_upload_for_her_is_recorded_and_the_shared_cache_is_not(
    real_db, bucket
):
    with usage.usage_scope(user_id="tw-track"):
        await storage.upload_media(b"x", "audio/katha-welcome.ogg")
        await storage.upload_media(b"x", "system/speech/m/s/hi-IN/abc.ogg")
    keys = (
        (
            await real_db.execute(
                select(MediaObject.s3_key).where(MediaObject.user_id == "tw-track")
            )
        )
        .scalars()
        .all()
    )
    assert keys == ["audio/katha-welcome.ogg"]


async def test_erasure_leaves_nothing_of_hers_in_the_bucket_or_at_twilio(
    real_db, bucket
):
    """M-08: the consent welcome was uploaded and never found. List the
    bucket afterwards rather than trusting the report."""
    parent = await _consented_parent(real_db)
    with usage.usage_scope(user_id=parent.user_id):
        # What the adapter does for the welcome, and for an ordinary turn.
        await storage.upload_media(b"welcome", "audio/katha-welcome1.ogg")
        await storage.upload_media(b"reply", "audio/katha-reply1.ogg")
    await storage.upload_media(b"shared", "system/speech/m/s/en-IN/x.ogg")
    sid = f"MM{uuid.uuid4().hex[:12]}"
    await twilio_cleanup.queue(
        sid, direction=twilio_cleanup.INBOUND, user_id=parent.user_id
    )
    adapter = _adapter()

    with patch("adapters.whatsapp_stub.get_whatsapp_adapter", return_value=adapter):
        async with AsyncSessionLocal() as db:
            report = await erasure.erase_user(parent.user_id, db, actor="test")

    assert report.complete, report.failures
    assert set(bucket.objects) == {"system/speech/m/s/en-IN/x.ogg"}, (
        "only the shared cache is left"
    )
    adapter.delete_message.assert_awaited_once_with(sid)
    real_db.expire_all()
    assert (
        await real_db.execute(
            select(MediaObject).where(MediaObject.user_id == parent.user_id)
        )
    ).first() is None


async def test_a_twilio_delete_that_fails_at_erasure_is_kept_without_her_name(
    real_db, bucket
):
    parent = await _consented_parent(real_db)
    sid = f"MM{uuid.uuid4().hex[:12]}"
    await twilio_cleanup.queue(
        sid, direction=twilio_cleanup.INBOUND, user_id=parent.user_id
    )
    with patch(
        "adapters.whatsapp_stub.get_whatsapp_adapter",
        return_value=_adapter(RuntimeError("503")),
    ):
        async with AsyncSessionLocal() as db:
            report = await erasure.erase_user(parent.user_id, db, actor="test")

    assert f"twilio:{sid}" in report.failures
    queued = await _queued(real_db, sid)
    assert queued is not None and queued.user_id == "DELETED"


# ── a card whose send failed is still tracked ────────────────────────────────


async def test_a_failed_card_send_keeps_the_key_so_erasure_finds_it(real_db, bucket):
    parent = await _consented_parent(real_db)
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == parent.user_id)
        )
    ).scalar_one()
    profile.family_whatsapp_number = "+919000000500"
    profile.family_number_confirmed_at = datetime.now(timezone.utc)
    session = Session(
        id=uuid.uuid4(),
        user_id=parent.user_id,
        session_number=1,
        domain="childhood",
        exchange_count=2,
        status="completed",
        started_at=datetime.now(timezone.utc),
    )
    real_db.add(session)
    await real_db.commit()
    # Plain values: the session is expired below, and an expired row's
    # attributes cannot be read outside a greenlet.
    session_id = session.id
    real_db.add(
        StoryAtom(
            session_id=session_id,
            user_id=parent.user_id,
            domain="childhood",
            narrative="By the river.",
            who=[],
            open_threads=[],
            completeness_score=4,
            verbatim_quote="the river was our whole world",
        )
    )
    await real_db.commit()

    whatsapp = MagicMock()
    whatsapp.send_image = AsyncMock(side_effect=RuntimeError("twilio down"))
    with patch("core.orchestrator.get_whatsapp_adapter", return_value=whatsapp):
        await orchestrator._generate_and_deliver_memory_card(
            str(session_id), parent.user_id, real_db
        )

    real_db.expire_all()
    card = (
        await real_db.execute(
            select(MemoryCard).where(MemoryCard.session_id == session_id)
        )
    ).scalar_one()
    key = card.image_s3_key
    assert card.delivery_status == "failed"
    assert key in bucket.objects

    async with AsyncSessionLocal() as db:
        await erasure.erase_user(parent.user_id, db, actor="test")
    assert key not in bucket.objects
