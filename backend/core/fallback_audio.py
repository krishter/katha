from __future__ import annotations

import asyncio
import logging
from enum import Enum

from config import settings
from media import system_speech, tts, voices
from prompts.system_prompt import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)


class FailureStage(str, Enum):
    STT = "stt"
    LLM = "llm"
    OTHER = "other"


_MESSAGES: dict[FailureStage, str] = {
    FailureStage.STT: (
        "I'm sorry, I couldn't quite hear that. Could you try sending it again?"
    ),
    FailureStage.LLM: (
        "I'm having a little trouble right now. Give me a moment and try again?"
    ),
    FailureStage.OTHER: (
        "Something went wrong on my side. I'll be here tomorrow at our usual time."
    ),
}

_DEFAULT_LANGUAGE = "en-IN"

# (stage, language_code, voice pin) -> pre-synthesized OGG/Opus bytes.
# Populated once at startup by preload_fallback_audio — a live failure must
# never depend on a live TTS call to apologise for a failure (which may
# itself be TTS's). Keyed by voice so the apology is in the voice she knows.
_cache: dict[tuple[FailureStage, str, str], bytes] = {}

# Preloading is TTS calls, one per clip; a few at a time keeps the first
# boot after a voice change to well under a minute.
_PRELOAD_CONCURRENCY = 6


def get_fallback_text(stage: FailureStage) -> str:
    return _MESSAGES[stage]


def _english_like(voice: voices.Voice, gender: str) -> voices.Voice:
    """The same vendor's English voice for the persona: a Gemini voice says
    every language, a Sarvam one has a separate English speaker."""
    if voice.vendor == voices.GOOGLE:
        return voices.Voice(
            voice.model, voice.speaker, _DEFAULT_LANGUAGE, vendor=voices.GOOGLE
        )
    return voices.sarvam_counterpart(_DEFAULT_LANGUAGE, gender, settings.TTS_MODEL)


def get_fallback_audio(
    stage: FailureStage,
    language_code: str,
    gender: str | None,
    voice: voices.Voice | None = None,
) -> bytes | None:
    """Return pre-synthesized fallback audio for (stage, language_code) in
    `voice` (the parent's pinned voice; by default a new parent's), falling
    back to English in the same vendor and persona, or None if neither was
    successfully cached."""
    gender = voices.normalise_gender(gender)
    voice = voice or tts.default_voice(language_code, gender)
    return _cache.get((stage, language_code, voice.pin)) or _cache.get(
        (stage, _DEFAULT_LANGUAGE, _english_like(voice, gender).pin)
    )


def _preload_voices(language_code: str, gender: str) -> list[voices.Voice]:
    """The voices a parent in this language and persona can be pinned to
    today: a new parent's, and the Sarvam voice that existing parents were
    pinned to at the switch (and that a rollback moves them back to)."""
    found = [tts.default_voice(language_code, gender)]
    sarvam = voices.sarvam_counterpart(language_code, gender, settings.TTS_MODEL)
    if sarvam not in found:
        found.append(sarvam)
    return found


async def preload_fallback_audio() -> None:
    """
    Synthesize every fallback message in every supported language, in each
    voice a parent can be pinned to, once, at startup. A failure to
    pre-synthesize one clip is logged but does not block startup —
    get_fallback_audio degrades to English, and the caller degrades further
    to text if even that is unavailable.
    """
    limit = asyncio.Semaphore(_PRELOAD_CONCURRENCY)

    async def preload(stage, text, language_code, gender, voice) -> None:
        async with limit:
            try:
                # Through the system-speech cache: synthesised once and kept
                # in S3, so a restart does not re-synthesise them.
                _cache[
                    (stage, language_code, voice.pin)
                ] = await system_speech.get_system_speech(
                    text,
                    language_code,
                    gender=gender,
                    purpose="tts_fallback",
                    voice=voice,
                )
            except Exception:
                logger.error(
                    "Failed to pre-synthesize fallback audio for stage=%s "
                    "language=%s voice=%s",
                    stage.value,
                    language_code,
                    voice.pin,
                    exc_info=True,
                )

    await asyncio.gather(
        *(
            preload(stage, text, language_code, gender, voice)
            for stage, text in _MESSAGES.items()
            for language_code in SUPPORTED_LANGUAGES
            for gender in voices.GENDERS
            for voice in _preload_voices(language_code, gender)
        )
    )
    logger.info("Pre-synthesized %d fallback audio clips", len(_cache))
