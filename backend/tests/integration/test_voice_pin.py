"""
The pinned voice against real Postgres (docs/specs/GEMINI_TTS.md §4.2): the
column's insert-time default, and a move by scripts/set_voice.py.
"""

from __future__ import annotations

import uuid
from datetime import time

import pytest
from sqlalchemy import select

from models.user_profile import UserProfileModel
from models.voice_change import VoiceChange
from scripts import set_voice

pytestmark = pytest.mark.integration


def _profile(user_id: str, language: str, gender: str, **extra) -> UserProfileModel:
    return UserProfileModel(
        user_id=user_id,
        name="Saraswathy",
        whatsapp_number="+919000000211",
        preferred_language=language,
        katha_gender=gender,
        scheduled_time=time(10, 30),
        **extra,
    )


@pytest.fixture
def gemini_default(monkeypatch):
    monkeypatch.setattr("config.settings.TTS_VENDOR", "gemini")
    monkeypatch.setattr("config.settings.GEMINI_TTS_LANGUAGES", "all")
    monkeypatch.setattr("config.settings.TTS_MODEL", "bulbul:v4-flash")


async def test_a_profile_inserted_without_a_pin_gets_one(real_db, gemini_default):
    """Scripts and seeds that never mention the voice still pin one."""
    user_id = f"pin-{uuid.uuid4().hex[:8]}"
    real_db.add(_profile(user_id, "ml-IN", "male"))
    await real_db.commit()

    row = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    assert row.tts_voice == "google/gemini-3.8-flash-lite-tts/Achird"


async def test_a_profile_inserted_with_a_pin_keeps_it(real_db, gemini_default):
    user_id = f"pin-{uuid.uuid4().hex[:8]}"
    pin = "sarvam/bulbul:v3/ritu"
    real_db.add(_profile(user_id, "ta-IN", "female", tts_voice=pin))
    await real_db.commit()

    row = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    assert row.tts_voice == pin


async def test_set_voice_moves_a_parent_and_records_it(real_db, gemini_default):
    user_id = f"pin-{uuid.uuid4().hex[:8]}"
    before = "sarvam/bulbul:v4-flash/ritu_hi_conversation"
    real_db.add(_profile(user_id, "hi-IN", "female", tts_voice=before))
    await real_db.commit()

    profiles = list(
        (
            await real_db.execute(
                select(UserProfileModel).where(UserProfileModel.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    moves = set_voice.plan(profiles, "gemini")
    assert await set_voice.apply(real_db, profiles, moves, by="Krish", reason="D2") == 1

    real_db.expire_all()
    row = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    assert row.tts_voice == "google/gemini-3.8-flash-lite-tts/Sulafat"
    (change,) = (
        (
            await real_db.execute(
                select(VoiceChange).where(VoiceChange.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    assert (change.from_voice, change.changed_by, change.reason) == (
        before,
        "Krish",
        "D2",
    )
    assert change.created_at is not None
