"""
P7 (M-07): nothing that identifies her, or repeats what she said, goes into
a log. Logs sit on the box outside every erasure.

One parent, end to end through the real webhook and the stub WhatsApp
adapter, with every logger at DEBUG: the welcome, her yes and its
confirmation, a full voice turn with extraction, and a magic-link request.
Her words, Katha's reply and the model's raw output each carry a marker
word; none of the markers, her number, her child's number or the family
email may appear in any log record or exception text.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from main import app
from models.db import AsyncSessionLocal, get_db
from models.family_account import FamilyAccount
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

_HER_WORDS = "QUINCEBLOSSOM"  # in everything she says
_REPLY_WORDS = "LANTERNGLOW"  # in Katha's dialogue reply
_MODEL_WORDS = "MOTHWING"  # in the model's raw extraction output
_NUMBER = f"+9194{uuid.uuid4().int % 100000000:08d}"
_FAMILY = "+919000000611"
_EMAIL = f"pii-{uuid.uuid4().hex[:6]}@example.test"


class _Model:
    """Dialogue, extraction (unparseable once, to reach the error path) and
    the consent classifier, each saying its marker word."""

    async def __call__(self, messages, system=None, **kwargs):
        purpose = kwargs.get("purpose")
        prompt = messages[-1].content if messages else ""
        if purpose == "parent_answer_classify":
            reply = prompt.split("HER REPLY\n", 1)[1].split("\n\nRespond with", 1)[0]
            label = "yes" if reply.startswith("Yes") else "none"
            content = json.dumps(
                {"label": label, "span": reply if label == "yes" else ""}
            )
        elif system is not None:
            content = (
                f"<response>How lovely, the {_REPLY_WORDS}. Tell me more?</response>"
            )
        elif purpose in ("extraction", "extraction_retry"):
            content = f"not json at all, {_MODEL_WORDS} {_HER_WORDS}"
        else:
            content = f"{{broken {_MODEL_WORDS}"
        return SimpleNamespace(
            content=content, input_tokens=5, output_tokens=5, stop_reason="end_turn"
        )


async def _post(client, transcript: str, *, audio=True):
    data = {"From": f"whatsapp:{_NUMBER}", "MessageSid": f"SM{uuid.uuid4().hex[:12]}"}
    if audio:
        data["MediaUrl0"] = "https://example.test/a.ogg"
        data["MediaContentType0"] = "audio/ogg"
    else:
        data["Body"] = transcript
    stt = AsyncMock(
        return_value=SimpleNamespace(
            transcript=transcript, language_code="en-IN", language_probability=1
        )
    )
    with (
        patch("adapters.sarvam_stt.transcribe", new=stt),
        patch("core.orchestrator.sarvam_stt.transcribe", new=stt),
        patch("api.routes.webhook.sarvam_stt.transcribe", new=stt),
    ):
        response = await client.post("/webhook/whatsapp", data=data)
    assert response.status_code == 200


async def test_a_parent_s_whole_first_day_leaves_nothing_personal_in_the_logs(
    real_db, caplog, monkeypatch
):
    # What production logs. Off production, a mocked email is logged whole,
    # because a developer signs in with the link in it.
    monkeypatch.setattr("config.settings.ENVIRONMENT", "production")
    user_id = f"pii-{uuid.uuid4().hex[:8]}"
    real_db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=_NUMBER,
            family_whatsapp_number=_FAMILY,
            preferred_language="en-IN",
            onboarding_context="",
            scheduled_time=time(9, 30),
        )
    )
    real_db.add(
        FamilyAccount(
            email=_EMAIL, user_id=user_id, plan="free", onboarding_complete=True
        )
    )
    await real_db.commit()

    async def _db():
        async with AsyncSessionLocal() as s:
            yield s

    async def _tts(text, language_code=None, **_kwargs):
        return b"wav"

    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _db
    caplog.set_level(logging.DEBUG)
    try:
        with (
            patch("adapters.llm.chat", new=_Model()),
            patch("adapters.sarvam_tts.synthesize", new=_tts),
            patch("adapters.gemini_tts.synthesize", new=_tts),
            patch(
                "media.audio_convert.convert_wav_to_ogg",
                new=AsyncMock(return_value=b"ogg"),
            ),
            patch(
                "core.orchestrator.convert_wav_to_ogg",
                new=AsyncMock(return_value=b"ogg"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await _post(client, f"Hello, {_HER_WORDS} here", audio=False)
                await _post(client, f"Yes, you can record, {_HER_WORDS}")
                await _post(client, f"Yes, that's right, {_HER_WORDS}")
                await _post(
                    client,
                    f"We had a {_HER_WORDS} tree behind the house in Kanpur.",
                )
                await _post(client, f"Just text, {_HER_WORDS}", audio=False)
                await client.post("/onboarding/start", data={"email": _EMAIL})
                await client.post("/auth/magic-link", data={"email": f"x{_EMAIL}"})
    finally:
        if prev is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = prev

    assert caplog.records, "the run logged nothing, so the guard proved nothing"
    leaks = []
    for record in caplog.records:
        text = record.getMessage()
        if record.exc_info:
            text += " " + logging.Formatter().formatException(record.exc_info)
        for secret in (
            _HER_WORDS,
            _REPLY_WORDS,
            _MODEL_WORDS,
            _NUMBER,
            _FAMILY,
            _EMAIL,
        ):
            if secret in text:
                leaks.append(f"{record.name}:{record.lineno} {secret}")
    assert not leaks, leaks
