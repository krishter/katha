"""
P3 (M-03, D3): the parent's number and the family's number, against real
Postgres.

- One profile per parent number: a unique index, a 409 from onboarding,
  and a migration that stops rather than pick between duplicates.
- After she has agreed, the family cannot move her number from the form.
- Memory cards are kept, not sent, until the child proves the family
  number is theirs by sending Katha a code from it.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select, text

from adapters.whatsapp_stub import get_whatsapp_adapter
from core import family_number, orchestrator, parent_consent
from core.auth import get_current_user
from main import app
from models.consent_record import ConsentRecord
from models.db import AsyncSessionLocal, get_db
from models.family_account import FamilyAccount
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

_FAMILY = "+919000000555"


def _number() -> str:
    return f"+9198{uuid.uuid4().int % 100000000:08d}"


async def _family(db, *, number: str | None = None, consented: bool = False):
    user_id = f"user_{uuid.uuid4().hex}"
    number = number or _number()
    db.add(
        FamilyAccount(
            email=f"{user_id}@example.test",
            user_id=user_id,
            plan="free",
            onboarding_complete=True,
        )
    )
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Lakshmi",
            whatsapp_number=number,
            family_whatsapp_number=_FAMILY,
            preferred_language="ta-IN",
            onboarding_context="",
            scheduled_time=time(9, 30),
        )
    )
    if consented:
        db.add(
            ConsentRecord(
                user_id=user_id,
                email_hash="",
                consent_version=parent_consent.PARENT_CONSENT_VERSION,
                principal="parent",
                whatsapp_number=number,
            )
        )
    await db.commit()
    return user_id, number


class _Api:
    """The real app as the signed-in family, with a stub WhatsApp."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.adapter = MagicMock()
        self.adapter.validate_signature = MagicMock(return_value=True)
        self.adapter.send_text = AsyncMock(return_value="SM_T")

    async def __aenter__(self):
        async def _db():
            async with AsyncSessionLocal() as s:
                yield s

        self._prev = {
            dep: app.dependency_overrides.get(dep)
            for dep in (get_db, get_current_user, get_whatsapp_adapter)
        }
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": self.user_id,
            "sub": f"{self.user_id}@example.test",
        }
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

    async def profile(self, whatsapp_number: str, **extra) -> httpx.Response:
        return await self.client.post(
            "/onboarding/profile",
            data={
                "parent_name": "Lakshmi",
                "whatsapp_number": whatsapp_number,
                "family_whatsapp_number": _FAMILY,
                "preferred_language": "ta-IN",
                "session_time": "09:30",
                **extra,
            },
        )

    def replies(self) -> str:
        return " ".join(str(c.args[1]) for c in self.adapter.send_text.await_args_list)


# ── one profile per number ───────────────────────────────────────────────────


async def test_a_second_family_cannot_register_the_same_number(real_db):
    _, number = await _family(real_db)
    other, _ = await _family(real_db)

    async with _Api(other) as api:
        response = await api.profile(number)
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


async def test_the_database_refuses_a_duplicate_too(real_db):
    _, number = await _family(real_db)
    real_db.add(
        UserProfileModel(
            user_id=f"user_{uuid.uuid4().hex}",
            name="Someone else",
            whatsapp_number=number,
            preferred_language="hi-IN",
            onboarding_context="",
            scheduled_time=time(9, 30),
        )
    )
    with pytest.raises(Exception, match="uq_user_profiles_whatsapp_number"):
        await real_db.commit()
    await real_db.rollback()


async def test_the_migration_stops_on_existing_duplicates(real_db):
    """It names the user_ids and picks no winner. Run inside a transaction
    that drops the index, seeds a duplicate and rolls back."""
    migration = importlib.import_module(
        "migrations.versions.b8e3f1c5d7a2_parent_consent_integrity"
    )
    async with AsyncSessionLocal() as s:
        connection = await s.connection()
        await connection.execute(text("DROP INDEX uq_user_profiles_whatsapp_number"))
        for user_id in ("dup-a", "dup-b"):
            await connection.execute(
                text(
                    "INSERT INTO user_profiles (id, user_id, name, whatsapp_number, "
                    "preferred_language, onboarding_context, scheduled_time, "
                    "timezone, tts_voice) VALUES (gen_random_uuid(), :u, 'X', "
                    "'+919000000999', 'hi-IN', '', '09:30', 'Asia/Kolkata', 'x')"
                ),
                {"u": user_id},
            )
        found = await connection.run_sync(migration.duplicate_numbers)
        await s.rollback()
    assert found == {"+919000000999": ["dup-a", "dup-b"]}


# ── her number, after she has agreed ─────────────────────────────────────────


async def test_the_family_cannot_move_her_number_after_consent(real_db):
    user_id, number = await _family(real_db, consented=True)

    async with _Api(user_id) as api:
        response = await api.profile(_number())
        assert response.status_code == 409
        assert "Contact Katha" in response.json()["detail"]
        # Resending setup with her number unchanged is fine.
        assert (await api.profile(number)).status_code == 200


async def test_before_consent_a_new_number_starts_from_the_welcome(real_db):
    user_id, _ = await _family(real_db)
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    profile.parent_consent_asks = 1
    await real_db.commit()

    async with _Api(user_id) as api:
        assert (await api.profile(_number())).status_code == 200

    real_db.expire_all()
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    assert profile.parent_consent_asks == 0


async def test_onboarding_records_what_she_calls_the_child(real_db):
    user_id, number = await _family(real_db)
    async with _Api(user_id) as api:
        assert (await api.profile(number, child_name=" Priya ")).status_code == 200
    real_db.expire_all()
    account = (
        await real_db.execute(
            select(FamilyAccount).where(FamilyAccount.user_id == user_id)
        )
    ).scalar_one()
    assert account.child_name == "Priya"


# ── the family number ────────────────────────────────────────────────────────


async def _session_with_a_story(db, user_id: str) -> str:
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=1,
        domain="childhood",
        exchange_count=3,
        status="completed",
        started_at=datetime.now(timezone.utc),
    )
    db.add(session)
    await db.commit()
    db.add(
        StoryAtom(
            session_id=session.id,
            user_id=user_id,
            domain="childhood",
            narrative="Lived near the river at Kumbakonam.",
            who=[],
            open_threads=[],
            completeness_score=4,
            verbatim_quote="the street smelled of jasmine",
        )
    )
    await db.commit()
    return str(session.id)


async def _make_card(db, user_id: str):
    # The webhook confirmed the number in its own session; read it fresh.
    db.expire_all()
    session_id = await _session_with_a_story(db, user_id)
    whatsapp = MagicMock()
    whatsapp.send_image = AsyncMock(return_value="SM_CARD")
    with (
        patch("core.orchestrator.storage.upload_media", new=AsyncMock()),
        patch("core.orchestrator.get_whatsapp_adapter", return_value=whatsapp),
    ):
        await orchestrator._generate_and_deliver_memory_card(session_id, user_id, db)
    db.expire_all()
    card = (
        await db.execute(
            select(MemoryCard).where(MemoryCard.session_id == uuid.UUID(session_id))
        )
    ).scalar_one()
    return card, whatsapp


async def test_a_card_for_an_unconfirmed_number_is_kept_not_sent(real_db):
    user_id, _ = await _family(real_db, consented=True)

    card, whatsapp = await _make_card(real_db, user_id)

    whatsapp.send_image.assert_not_awaited()
    assert card.twilio_message_sid is None
    assert card.image_s3_key, "kept for the dashboard"


async def test_confirming_the_number_lets_the_next_card_go(real_db):
    user_id, parent_number = await _family(real_db, consented=True)

    async with _Api(user_id) as api:
        link = (await api.client.post("/family/family-number/confirm-link")).json()[
            "link"
        ]
        code = link.rsplit("CONFIRM%20", 1)[1]

        # From the wrong phone, the code proves nothing.
        await api.client.post(
            "/webhook/whatsapp",
            data={
                "From": "whatsapp:+919000000556",
                "Body": f"CONFIRM {code}",
                "MessageSid": f"SM{uuid.uuid4().hex[:12]}",
            },
        )
        real_db.expire_all()
        profile = (
            await real_db.execute(
                select(UserProfileModel).where(UserProfileModel.user_id == user_id)
            )
        ).scalar_one()
        assert not family_number.is_confirmed(profile)
        assert family_number.NOT_CONFIRMED_TEXT in api.replies()

        # From the family's own phone, it does.
        await api.client.post(
            "/webhook/whatsapp",
            data={
                "From": f"whatsapp:{_FAMILY}",
                "Body": f"confirm {code.lower()}",
                "MessageSid": f"SM{uuid.uuid4().hex[:12]}",
            },
        )
        assert family_number.CONFIRMED_TEXT in api.replies()

        stats = (await api.client.get("/family/stats")).json()
        assert stats["family_number_confirmed"] is True

    card, whatsapp = await _make_card(real_db, user_id)
    whatsapp.send_image.assert_awaited_once()
    assert whatsapp.send_image.await_args.kwargs["to_number"] == _FAMILY
    assert card.twilio_message_sid == "SM_CARD"
    assert parent_number != _FAMILY


async def test_an_expired_code_confirms_nothing(real_db):
    user_id, _ = await _family(real_db, consented=True)
    account = (
        await real_db.execute(
            select(FamilyAccount).where(FamilyAccount.user_id == user_id)
        )
    ).scalar_one()
    code = family_number.issue_code(
        account, now=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    await real_db.commit()

    assert await family_number.confirm(_FAMILY, code, real_db) is None


async def test_a_new_family_number_must_be_confirmed_again(real_db):
    user_id, number = await _family(real_db, consented=True)
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    profile.family_number_confirmed_at = datetime.now(timezone.utc)
    await real_db.commit()

    async with _Api(user_id) as api:
        response = await api.profile(number, family_whatsapp_number="+919000000557")
    assert response.status_code == 200

    real_db.expire_all()
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    assert profile.family_number_confirmed_at is None
