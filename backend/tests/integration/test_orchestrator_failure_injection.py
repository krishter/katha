"""
Real-DB failure-injection suite (REMEDIATION_PLAN WS5.2) — for each stage of
process_voice_turn where a failure's effect on Turn-row persistence actually
depends on real commit timing (not just mocked-call assertions), verifies:

  1. The caller still gets a reply (never silence — P2).
  2. An ERROR-level log record was emitted with session context.
  3. No partial/corrupt Turn row is left behind: STT/LLM failures happen
     before _persist_turn is ever called, so zero Turn rows should exist;
     TTS/ffmpeg failures happen after _persist_turn (see orchestrator.py
     process_voice_turn step 10 vs. step 12), so exactly one complete Turn
     row should exist.

Delivery-layer failures (S3 upload inside send_voice_note, Twilio send) are
covered by unit-level tests in test_webhook.py instead — those happen after
the turn is already committed, so no row-integrity question is at stake
there, only "does the user still get a reply."
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from core import orchestrator, session_manager
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.system_prompt import UserProfile

pytestmark = pytest.mark.integration

_FAKE_WAV = b"RIFF" + b"\x00" * 40
_FAKE_EMBEDDING = [0.01] * 1536
_DIALOGUE_CONTENT = "<response>Tell me more about that.</response>"


async def _fresh_session(db):
    user_id = f"failure-test-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number="+919876500001",
            preferred_language="en-IN",
            onboarding_context="",
            family_whatsapp_number="+919876511112",
            scheduled_time=time(10, 30),
        )
    )
    await db.commit()
    state = await session_manager.start_session(user_id, db)
    profile = UserProfile(
        name="Kamala", preferred_language="en-IN", onboarding_context=""
    )
    return state.session_id, profile


async def _turn_count(db, session_id: str) -> int:
    result = await db.execute(
        select(Turn).where(Turn.session_id == uuid.UUID(session_id))
    )
    return len(result.scalars().all())


_stt_ok = AsyncMock(
    return_value=SimpleNamespace(
        transcript="I remember the old well in our courtyard.",
        language_code="en-IN",
        language_probability=0.95,
    )
)


async def _llm_ok(messages, system=None, max_tokens=500):
    return SimpleNamespace(content=_DIALOGUE_CONTENT, input_tokens=10, output_tokens=10)


_tts_ok = AsyncMock(return_value=_FAKE_WAV)
_convert_ok = AsyncMock(side_effect=lambda audio_bytes: audio_bytes)


@pytest.mark.parametrize(
    "stage,patches,expect_turn_rows",
    [
        (
            "stt",
            {
                "core.orchestrator.sarvam_stt.transcribe": AsyncMock(
                    side_effect=RuntimeError("Sarvam STT down")
                )
            },
            0,
        ),
        (
            "llm",
            {
                "core.orchestrator.llm.chat": AsyncMock(
                    side_effect=RuntimeError("Anthropic down")
                )
            },
            0,
        ),
        (
            "tts",
            {
                "core.orchestrator.sarvam_tts.synthesize": AsyncMock(
                    side_effect=RuntimeError("Sarvam TTS down")
                )
            },
            1,
        ),
        (
            "ffmpeg",
            {
                "core.orchestrator.convert_wav_to_ogg": AsyncMock(
                    side_effect=RuntimeError("ffmpeg crashed")
                )
            },
            1,
        ),
    ],
)
async def test_failure_stage_never_silent_and_never_corrupts_rows(
    real_db, caplog, stage, patches, expect_turn_rows
):
    db = real_db
    session_id, profile = await _fresh_session(db)

    active_patches = {
        "core.orchestrator.sarvam_stt.transcribe": _stt_ok,
        "core.orchestrator.llm.chat": AsyncMock(side_effect=_llm_ok),
        "core.orchestrator.sarvam_tts.synthesize": _tts_ok,
        "core.orchestrator.convert_wav_to_ogg": _convert_ok,
        # build_prior_context always embeds the query for semantic
        # retrieval, even on a fresh session with no prior atoms — must be
        # mocked regardless of which stage this case is injecting a
        # failure into, or every case makes a live (billed) OpenAI call.
        "memory.vector_store._embed": AsyncMock(return_value=_FAKE_EMBEDDING),
    }
    active_patches.update(patches)

    # A variable-length set of patches per stage, so ExitStack rather than
    # a fixed `with (a, b, c):` tuple.
    with contextlib.ExitStack() as stack:
        for target, mock_obj in active_patches.items():
            stack.enter_context(patch(target, new=mock_obj))

        result = await orchestrator.process_voice_turn(
            b"fake-audio-bytes",
            session_id,
            profile,
            db,
            inbound_message_sid=f"SM_FAIL_{stage}_{uuid.uuid4().hex[:6]}",
        )

    # 1. Never silent — always some reply, voice or text.
    assert result.response_text
    assert result.response_mime_type in ("audio/ogg", "audio/x-wav", "text/plain")

    # 2. ERROR-level log with session context.
    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert any(session_id in r.getMessage() for r in error_records), (
        f"expected an ERROR log mentioning session_id for stage={stage}, "
        f"got: {[r.getMessage() for r in error_records]}"
    )

    # 3. Row-integrity: zero Turn rows for STT/LLM failures (never reached
    # _persist_turn), exactly one complete row for TTS/ffmpeg failures
    # (persisted before those stages run).
    turn_count = await _turn_count(db, session_id)
    assert turn_count == expect_turn_rows

    if expect_turn_rows:
        turns_result = await db.execute(
            select(Turn).where(Turn.session_id == uuid.UUID(session_id))
        )
        turn = turns_result.scalar_one()
        assert turn.transcript
        assert turn.response_text
        assert turn.extraction_json is not None
