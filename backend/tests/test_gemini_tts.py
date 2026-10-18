"""
adapters/gemini_tts.py: the listening kit's Gemini 3.8 Flash TTS side
(docs/proposals/tts-strategy.md §5). Every vendor call is mocked.
"""

from __future__ import annotations

import array
import base64
import io
import math
import wave
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters import gemini_tts
from core import pricing

RATE = gemini_tts.SAMPLE_RATE


def pcm(*segments: tuple[str, float]) -> bytes:
    """Raw 24 kHz 16-bit mono PCM: ("tone"|"silence", seconds) segments."""
    samples = array.array("h")
    for kind, seconds in segments:
        n = int(RATE * seconds)
        if kind == "tone":
            samples.extend(
                int(8000 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(n)
            )
        else:
            samples.extend([0] * n)
    return samples.tobytes()


GOOD = pcm(("tone", 6))
BROKEN = pcm(("tone", 0.5), ("silence", 20), ("tone", 3))


def _response(audio: bytes) -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "steps": [
            {"type": "user_input", "content": []},
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "audio",
                        "mime_type": "audio/l16",
                        "data": base64.b64encode(audio).decode(),
                    }
                ],
            },
        ]
    }
    return response


@pytest.fixture
def gemini(monkeypatch):
    """A Gemini endpoint that answers with the queued audio, in order."""
    monkeypatch.setattr("adapters.gemini_tts.settings.GEMINI_API_KEY", "test-key")
    queue: list[bytes] = []
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(side_effect=lambda *a, **k: _response(queue.pop(0)))
    with patch("adapters.gemini_tts.httpx.AsyncClient", return_value=client):
        yield SimpleNamespace(client=client, queue=queue)


async def test_request_follows_the_interactions_api(gemini, usage_rows):
    gemini.queue.append(GOOD)
    await gemini_tts.synthesize(
        "Namaste. What do you remember?", "hi-IN", gender="female", purpose="eval"
    )

    call = gemini.client.post.call_args
    assert call.args[0] == (
        "https://generativelanguage.googleapis.com/v1beta/interactions"
    )
    assert call.kwargs["headers"]["x-goog-api-key"] == "test-key"
    body = call.kwargs["json"]
    assert body["model"] == "gemini-3.8-flash-tts"
    (turn,) = body["input"]
    assert turn["type"] == "user_input"
    (part,) = turn["content"]
    assert part == {
        "type": "text",
        "text": "Namaste. What do you remember?",
        "annotations": [{"type": "speech_metadata", "style": gemini_tts.STYLE}],
    }
    assert body["response_format"] == {
        "type": "audio",
        "mime_type": "audio/l16",
        "sample_rate": 24000,
    }
    assert body["generation_config"] == {"speech_config": [{"voice": "Sulafat"}]}
    # No language field: the model detects the language from the text.
    assert "language" not in str(body).lower()


@pytest.mark.parametrize(
    "gender,voice", [("female", "Sulafat"), ("male", "Achird"), (None, "Sulafat")]
)
async def test_one_voice_per_persona(gemini, usage_rows, gender, voice):
    gemini.queue.append(GOOD)
    await gemini_tts.synthesize("Hello.", "ta-IN", gender=gender, purpose="eval")
    body = gemini.client.post.call_args.kwargs["json"]
    assert body["generation_config"]["speech_config"] == [{"voice": voice}]


async def test_the_style_can_be_overridden_for_one_call(gemini, usage_rows):
    gemini.queue.append(GOOD)
    await gemini_tts.synthesize(
        "Hello.", "en-IN", gender="female", purpose="eval", style="slow"
    )
    part = gemini.client.post.call_args.kwargs["json"]["input"][0]["content"][0]
    assert part["annotations"] == [{"type": "speech_metadata", "style": "slow"}]


async def test_raw_pcm_comes_back_as_a_24khz_wav(gemini, usage_rows):
    gemini.queue.append(GOOD)
    wav = await gemini_tts.synthesize(
        "Hello.", "en-IN", gender="female", purpose="eval"
    )
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getsampwidth(), w.getnchannels()) == (24000, 2, 1)
        assert w.readframes(w.getnframes()) == GOOD


async def test_a_wav_answer_is_passed_through_unwrapped(gemini, usage_rows):
    already_wav = gemini_tts.pcm_to_wav(GOOD)
    gemini.queue.append(already_wav)
    wav = await gemini_tts.synthesize(
        "Hello.", "en-IN", gender="female", purpose="eval"
    )
    assert wav == already_wav


async def test_a_mostly_silent_clip_is_synthesised_again_once(gemini, usage_rows):
    gemini.queue += [BROKEN, GOOD]
    wav = await gemini_tts.synthesize(
        "x" * 80, "ml-IN", gender="female", purpose="eval"
    )
    assert gemini.client.post.await_count == 2
    with wave.open(io.BytesIO(wav)) as w:
        assert w.readframes(w.getnframes()) == GOOD
    # Both attempts were paid for.
    assert len(usage_rows) == 2


async def test_a_clip_failing_twice_is_returned_anyway(gemini, usage_rows):
    gemini.queue += [BROKEN, BROKEN]
    wav = await gemini_tts.synthesize("x" * 80, "or-IN", gender="male", purpose="eval")
    assert gemini.client.post.await_count == 2
    assert gemini_tts.wav_seconds(wav) == pytest.approx(23.5, abs=0.01)


async def test_emoji_are_not_sent(gemini, usage_rows):
    gemini.queue.append(GOOD)
    await gemini_tts.synthesize(
        "Lovely! 😊 Tell me more?", "en-IN", gender="female", purpose="eval"
    )
    part = gemini.client.post.call_args.kwargs["json"]["input"][0]["content"][0]
    assert part["text"] == "Lovely! Tell me more?"


async def test_each_call_is_a_ledger_row_with_seconds_and_characters(
    gemini, usage_rows
):
    gemini.queue.append(GOOD)
    text = "Namaste."
    await gemini_tts.synthesize(text, "hi-IN", gender="female", purpose="eval")

    (row,) = usage_rows
    assert (row["vendor"], row["operation"], row["model"]) == (
        "google",
        "eval",
        "gemini-3.8-flash-tts",
    )
    assert row["units"] == {
        "audio_seconds": 6.0,
        "input_characters": len(text) + len(gemini_tts.STYLE),
    }


async def test_flash_lite_is_requested_and_ledgered_under_its_own_name(
    gemini, usage_rows
):
    gemini.queue.append(GOOD)
    await gemini_tts.synthesize(
        "Hello.",
        "ml-IN",
        gender="female",
        purpose="eval",
        model=gemini_tts.LITE_MODEL,
    )
    body = gemini.client.post.call_args.kwargs["json"]
    assert body["model"] == "gemini-3.8-flash-lite-tts"
    # Same voice and style as Flash, so a pair differs only by model.
    assert body["generation_config"] == {"speech_config": [{"voice": "Sulafat"}]}
    (row,) = usage_rows
    assert row["model"] == "gemini-3.8-flash-lite-tts"


async def test_an_unknown_model_is_refused_before_any_call(gemini, usage_rows):
    with pytest.raises(ValueError, match="gemini-9-tts"):
        await gemini_tts.synthesize(
            "Hello.", "en-IN", gender="female", purpose="eval", model="gemini-9-tts"
        )
    gemini.client.post.assert_not_called()


async def test_no_key_fails_before_any_call(monkeypatch):
    monkeypatch.setattr("adapters.gemini_tts.settings.GEMINI_API_KEY", "")
    with patch("adapters.gemini_tts.httpx.AsyncClient") as client:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            await gemini_tts.synthesize(
                "Hello.", "en-IN", gender="female", purpose="eval"
            )
    client.assert_not_called()


async def test_an_http_error_raises(gemini, monkeypatch):
    error = MagicMock(status_code=400, text="bad request")
    gemini.client.post = AsyncMock(return_value=error)
    with pytest.raises(RuntimeError, match="Gemini TTS error 400"):
        await gemini_tts.synthesize("Hello.", "en-IN", gender="female", purpose="eval")


# ── pricing ──────────────────────────────────────────────────────────────────


def _row(on: datetime, seconds=15.0, characters=200):
    return SimpleNamespace(
        vendor="google",
        operation="eval",
        model="gemini-3.8-flash-tts",
        units={"audio_seconds": seconds, "input_characters": characters},
        created_at=on,
    )


def test_gemini_audio_is_billed_at_the_measured_32_tokens_a_second(monkeypatch):
    monkeypatch.setattr("core.pricing.settings.USD_INR_RATE", 100.0)
    row = _row(datetime(2026, 10, 17, tzinfo=timezone.utc))
    # 15 s x 32 tokens x $9/M + 200 chars x $0.50/M = $0.00432 + $0.0001.
    assert pricing.cost_breakdown(row) == {"google_tts": Decimal("0.4420")}


def test_gemini_prices_double_from_2027(monkeypatch):
    monkeypatch.setattr("core.pricing.settings.USD_INR_RATE", 100.0)
    before = pricing.cost_inr(_row(datetime(2026, 12, 31, 23, tzinfo=timezone.utc)))
    after = pricing.cost_inr(_row(datetime(2027, 1, 1, 0, 30, tzinfo=timezone.utc)))
    assert after == before * 2


def test_an_unknown_gemini_model_or_missing_units_is_unpriced():
    row = _row(datetime(2026, 10, 17, tzinfo=timezone.utc))
    row.model = "gemini-9-tts"
    assert pricing.cost_breakdown(row) is None
    row = _row(datetime(2026, 10, 17, tzinfo=timezone.utc))
    row.units = {"audio_seconds": 3}
    assert pricing.cost_breakdown(row) is None
    assert pricing.is_priced("google", "gemini-3.8-flash-tts")
    assert not pricing.is_priced("google", "gemini-9-tts")


def test_flash_lite_audio_is_two_thirds_of_flash(monkeypatch):
    monkeypatch.setattr("core.pricing.settings.USD_INR_RATE", 100.0)
    row = _row(datetime(2026, 10, 18, tzinfo=timezone.utc))
    row.model = "gemini-3.8-flash-lite-tts"
    # 15 s x 32 tokens x $6/M + 200 chars x $0.50/M = $0.00288 + $0.0001.
    assert pricing.cost_breakdown(row) == {"google_tts": Decimal("0.2980")}
    assert pricing.is_priced("google", "gemini-3.8-flash-lite-tts")
    later = _row(datetime(2027, 1, 1, 1, tzinfo=timezone.utc))
    later.model = "gemini-3.8-flash-lite-tts"
    assert pricing.cost_inr(later) == pricing.cost_inr(row) * 2
