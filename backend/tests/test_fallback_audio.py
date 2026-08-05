from unittest.mock import AsyncMock, patch

import pytest

from core import fallback_audio
from core.fallback_audio import (
    FailureStage,
    get_fallback_audio,
    get_fallback_text,
    preload_fallback_audio,
)


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
        patch(
            "core.fallback_audio.sarvam_tts.synthesize",
            new=AsyncMock(return_value=b"fake-wav"),
        ),
        patch(
            "core.fallback_audio.convert_wav_to_ogg",
            new=AsyncMock(return_value=b"fake-ogg"),
        ),
    ):
        await preload_fallback_audio()

    assert get_fallback_audio(FailureStage.STT, "hi-IN") == b"fake-ogg"
    assert get_fallback_audio(FailureStage.LLM, "ta-IN") == b"fake-ogg"


async def test_get_fallback_audio_falls_back_to_english_for_unknown_language():
    with (
        patch(
            "core.fallback_audio.sarvam_tts.synthesize",
            new=AsyncMock(return_value=b"fake-wav"),
        ),
        patch(
            "core.fallback_audio.convert_wav_to_ogg",
            new=AsyncMock(return_value=b"fake-ogg"),
        ),
    ):
        await preload_fallback_audio()

    # "xx-XX" was never synthesized — must fall back to en-IN's cached clip.
    assert get_fallback_audio(FailureStage.OTHER, "xx-XX") == b"fake-ogg"


async def test_get_fallback_audio_returns_none_when_nothing_cached():
    assert get_fallback_audio(FailureStage.STT, "zz-ZZ") is None


async def test_preload_does_not_raise_when_tts_fails_for_one_language():
    async def _flaky_synthesize(text, language_code):
        if language_code == "ta-IN":
            raise RuntimeError("Sarvam TTS is down")
        return b"fake-wav"

    with (
        patch(
            "core.fallback_audio.sarvam_tts.synthesize",
            new=AsyncMock(side_effect=_flaky_synthesize),
        ),
        patch(
            "core.fallback_audio.convert_wav_to_ogg",
            new=AsyncMock(return_value=b"fake-ogg"),
        ),
    ):
        # Should not raise even though one language failed.
        await preload_fallback_audio()

    assert get_fallback_audio(FailureStage.STT, "hi-IN") == b"fake-ogg"
