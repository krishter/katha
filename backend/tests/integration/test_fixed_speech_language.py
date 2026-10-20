"""
Every fixed message reaches a Tamil-speaking parent in Tamil (M-14,
PRE_PILOT_FIX_PLAN.md P2).

The crisis reply, the apologies and the "send me a voice message" prompt
were English literals handed to her Tamil voice. Every check that looked
at the language code passed, because the code was right and the words
were not. These drive the real webhook against real Postgres and read
what was actually handed to TTS (the vendor adapters are the boundary)
and to WhatsApp.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from speech import catalogue
from tests.integration.test_on_demand_session import (
    _NUMBER,
    _Harness,
    _seed_consented_parent,
)

pytestmark = pytest.mark.integration


def _stt(transcript: str):
    return patch(
        "core.orchestrator.sarvam_stt.transcribe",
        new=AsyncMock(
            return_value=SimpleNamespace(
                transcript=transcript,
                language_code="ta-IN",
                language_probability=0.95,
            )
        ),
    )


async def test_the_crisis_reply_is_spoken_and_sent_in_tamil(real_db):
    await _seed_consented_parent(real_db)

    async with _Harness() as h:
        with _stt("எனக்கு தற்கொலை எண்ணம் வருகிறது"):
            response = await h.inbound()
        assert response.status_code == 200
        texts = [str(c.args[1]) for c in h.adapter.send_text.await_args_list]

    assert catalogue.text("crisis", "ta-IN") in h.spoken, (
        f"TTS was handed: {[t[:60] for t in h.spoken]}"
    )
    assert catalogue.text("crisis_text", "ta-IN") in texts
    said = h.everything_said()
    assert "9152987821" in said
    assert "reach out to icall" not in said, "English read in a Tamil voice"


async def test_a_text_message_is_answered_in_tamil(real_db):
    await _seed_consented_parent(real_db)

    async with _Harness() as h:
        await h.client.post(
            "/webhook/whatsapp",
            data={
                "From": f"whatsapp:{_NUMBER}",
                "MessageSid": f"SM{uuid.uuid4().hex[:12]}",
                "Body": "vanakkam",
            },
        )
        texts = [str(c.args[1]) for c in h.adapter.send_text.await_args_list]

    assert catalogue.text("text_only", "ta-IN") in texts, texts


async def test_an_stt_failure_apologises_in_tamil(real_db):
    await _seed_consented_parent(real_db)

    async with _Harness() as h:
        with patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(side_effect=RuntimeError("Sarvam down")),
        ):
            await h.inbound()
        said = h.everything_said()

    assert catalogue.text("fallback_stt", "ta-IN").lower() in said, said[:200]
    assert "couldn't quite hear" not in said


async def test_an_unusable_reply_apologises_in_tamil(real_db):
    await _seed_consented_parent(real_db)

    async def _empty_dialogue(messages, system=None, **_kwargs):
        return SimpleNamespace(
            content="" if system is not None else "<extraction>{}</extraction>",
            input_tokens=10,
            output_tokens=0,
            stop_reason="end_turn",
        )

    async with _Harness() as h:
        with patch("adapters.llm.chat", new=_empty_dialogue):
            await h.inbound()

    assert catalogue.text("malformed", "ta-IN") in h.spoken, (
        f"TTS was handed: {[t[:60] for t in h.spoken]}"
    )
