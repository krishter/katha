"""
media/system_speech.py (COST_TO_SERVE.md §10): fixed phrases synthesised
once and cached by model, voice, language and text.

S3 is the in-memory stand-in from backend/conftest.py (`system_speech_store`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from media import system_speech


@pytest.fixture
def tts(monkeypatch):
    monkeypatch.setattr("media.system_speech.settings.TTS_MODEL", "bulbul:v3")
    synth = AsyncMock(return_value=b"WAV")
    with (
        patch("media.system_speech.sarvam_tts.synthesize", new=synth),
        patch(
            "media.system_speech.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda wav: b"OGG:" + wav),
        ),
    ):
        yield synth


async def test_a_miss_synthesises_once_and_stores_in_s3(tts, system_speech_store):
    audio = await system_speech.get_system_speech("We already talked today.", "hi-IN")
    assert audio == b"OGG:WAV"
    tts.assert_awaited_once()
    assert tts.await_args.kwargs["purpose"] == "tts_system"
    assert list(system_speech_store.values()) == [b"OGG:WAV"]


async def test_a_hit_skips_synthesis(tts):
    await system_speech.get_system_speech("We already talked today.", "hi-IN")
    again = await system_speech.get_system_speech("We already talked today.", "hi-IN")
    assert again == b"OGG:WAV"
    tts.assert_awaited_once()


async def test_after_a_restart_s3_answers_without_synthesis(tts, system_speech_store):
    await system_speech.get_system_speech("Sorry, I couldn't hear that.", "ta-IN")
    system_speech.clear_memory()  # a fresh process
    await system_speech.get_system_speech("Sorry, I couldn't hear that.", "ta-IN")
    tts.assert_awaited_once()


def test_key_includes_model_voice_language_and_text_hash(monkeypatch):
    monkeypatch.setattr("media.system_speech.settings.TTS_MODEL", "bulbul:v3")
    key = system_speech.speech_key("Hello", "hi-IN")
    assert key.startswith("system/speech/bulbul:v3/ritu/hi-IN/")
    assert key.endswith(".ogg") and len(key.rsplit("/", 1)[1]) == len(
        "0123456789ab.ogg"
    )
    assert system_speech.speech_key("Hello!", "hi-IN") != key  # text changes key

    monkeypatch.setattr("media.system_speech.settings.TTS_MODEL", "bulbul:v4-flash")
    assert system_speech.speech_key("Hello", "hi-IN").startswith(
        "system/speech/bulbul:v4-flash/ritu_hi_conversation/hi-IN/"
    )


async def test_s3_failures_never_cost_the_phrase(tts, monkeypatch):
    async def _down(*args):
        raise RuntimeError("S3 is down")

    monkeypatch.setattr(system_speech, "_s3_get", _down)
    monkeypatch.setattr(system_speech, "_s3_put", _down)
    audio = await system_speech.get_system_speech("Thank you.", "en-IN")
    assert audio == b"OGG:WAV"


async def test_fallback_preload_is_billed_as_tts_fallback(tts):
    from core import fallback_audio

    fallback_audio._cache.clear()
    await fallback_audio.preload_fallback_audio()
    assert {c.kwargs["purpose"] for c in tts.await_args_list} == {"tts_fallback"}
    # A second preload, as after a restart with S3 warm, synthesises nothing.
    calls = tts.await_count
    fallback_audio._cache.clear()
    system_speech.clear_memory()
    await fallback_audio.preload_fallback_audio()
    assert tts.await_count == calls
    fallback_audio._cache.clear()
