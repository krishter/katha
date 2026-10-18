from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from core import fallback_audio
from core.fallback_audio import (
    FailureStage,
    get_fallback_audio,
    get_fallback_text,
    preload_fallback_audio,
)


@contextmanager
def _vendors(mock):
    """Both TTS vendors answer with `mock`: new parents default to Gemini,
    and the preload also covers each persona's Sarvam voice."""
    with (
        patch("adapters.sarvam_tts.synthesize", new=mock),
        patch("adapters.gemini_tts.synthesize", new=mock),
    ):
        yield


@pytest.fixture(autouse=True)
def _clear_cache():
    """The module-level cache is shared process-wide — reset it around every
    test so preload results from one test can't leak into another."""
    fallback_audio._cache.clear()
    yield
    fallback_audio._cache.clear()


def test_get_fallback_text_returns_message_per_stage():
    assert "couldn't quite hear" in get_fallback_text(FailureStage.STT)
    assert "little trouble" in get_fallback_text(FailureStage.LLM)
    assert "tomorrow" in get_fallback_text(FailureStage.OTHER)


async def test_preload_populates_cache_for_every_language():
    with (
        _vendors(AsyncMock(return_value=b"fake-wav")),
        patch(
            "media.system_speech.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(return_value=b"fake-ogg"),
        ),
    ):
        await preload_fallback_audio()

    assert get_fallback_audio(FailureStage.STT, "hi-IN", "female") == b"fake-ogg"
    assert get_fallback_audio(FailureStage.LLM, "ta-IN", "male") == b"fake-ogg"


async def test_get_fallback_audio_falls_back_to_english_for_unknown_language():
    with (
        _vendors(AsyncMock(return_value=b"fake-wav")),
        patch(
            "media.system_speech.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(return_value=b"fake-ogg"),
        ),
    ):
        await preload_fallback_audio()

    # "xx-XX" was never synthesized — must fall back to en-IN's cached clip.
    assert get_fallback_audio(FailureStage.OTHER, "xx-XX", "male") == b"fake-ogg"


async def test_get_fallback_audio_returns_none_when_nothing_cached():
    assert get_fallback_audio(FailureStage.STT, "zz-ZZ", "female") is None


async def test_preload_does_not_raise_when_tts_fails_for_one_language():
    async def _flaky_synthesize(text, language_code, **_kwargs):
        if language_code == "ta-IN":
            raise RuntimeError("Sarvam TTS is down")
        return b"fake-wav"

    with (
        _vendors(AsyncMock(side_effect=_flaky_synthesize)),
        patch(
            "media.system_speech.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(return_value=b"fake-ogg"),
        ),
    ):
        # Should not raise even though one language failed.
        await preload_fallback_audio()

    assert get_fallback_audio(FailureStage.STT, "hi-IN", "female") == b"fake-ogg"


async def test_preload_synthesises_each_persona_and_serves_it_back():
    async def _by_gender(text, language_code, *, gender, **_kwargs):
        return gender.encode()

    with (
        _vendors(AsyncMock(side_effect=_by_gender)),
        patch(
            "media.system_speech.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda wav: wav),
        ),
    ):
        await preload_fallback_audio()

    assert get_fallback_audio(FailureStage.STT, "hi-IN", "male") == b"male"
    assert get_fallback_audio(FailureStage.STT, "hi-IN", "female") == b"female"
    # An unknown persona value gets the default voice, not nothing.
    assert get_fallback_audio(FailureStage.STT, "hi-IN", "") == b"female"


async def test_preload_covers_the_new_default_and_the_sarvam_voice():
    """A parent pinned to Gemini and one still on Sarvam (pinned at the
    switch) both hear an apology in their own voice."""
    from media import voices

    async def _by_vendor(text, language_code, **kwargs):
        return b"gemini" if "model" in kwargs else b"sarvam"

    with (
        _vendors(AsyncMock(side_effect=_by_vendor)),
        patch(
            "media.system_speech.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda wav: wav),
        ),
    ):
        await preload_fallback_audio()

    gemini = voices.gemini_voice("ml-IN", "female")
    sarvam = voices.voice_for("ml-IN", "female", "bulbul:v4-flash")
    assert get_fallback_audio(FailureStage.STT, "ml-IN", "female", gemini) == b"gemini"
    assert get_fallback_audio(FailureStage.STT, "ml-IN", "female", sarvam) == b"sarvam"
    # Urdu has no Gemini voice: its default is Sarvam.
    assert get_fallback_audio(FailureStage.STT, "ur-IN", "female") == b"sarvam"


async def test_an_unknown_language_falls_back_to_english_in_the_same_vendor():
    from media import voices

    async def _by_vendor(text, language_code, **kwargs):
        return b"gemini" if "model" in kwargs else b"sarvam"

    with (
        _vendors(AsyncMock(side_effect=_by_vendor)),
        patch(
            "media.system_speech.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda wav: wav),
        ),
    ):
        await preload_fallback_audio()

    gemini = voices.Voice(
        voices.GEMINI_TTS_MODEL, "Achird", "xx-XX", vendor=voices.GOOGLE
    )
    assert get_fallback_audio(FailureStage.LLM, "xx-XX", "male", gemini) == b"gemini"
