from __future__ import annotations

import logging
from enum import Enum

from media import system_speech, voices
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

# (stage, language_code, gender) -> pre-synthesized OGG/Opus bytes. Populated
# once at startup by preload_fallback_audio — a live failure must never
# depend on a live TTS call to apologise for a failure (which may itself be
# TTS's). Keyed by persona so the apology is in the voice she knows.
_cache: dict[tuple[FailureStage, str, str], bytes] = {}


def get_fallback_text(stage: FailureStage) -> str:
    return _MESSAGES[stage]


def get_fallback_audio(
    stage: FailureStage, language_code: str, gender: str | None
) -> bytes | None:
    """Return pre-synthesized fallback audio for (stage, language_code) in
    the persona's voice, falling back to English in the same persona, or
    None if neither was successfully cached."""
    gender = voices.normalise_gender(gender)
    return _cache.get((stage, language_code, gender)) or _cache.get(
        (stage, _DEFAULT_LANGUAGE, gender)
    )


async def preload_fallback_audio() -> None:
    """
    Synthesize every fallback message in every supported language, once, at
    startup. A failure to pre-synthesize one (stage, language) pair is
    logged but does not block startup — get_fallback_audio degrades to
    English, and the caller degrades further to text if even that is
    unavailable.
    """
    for stage, text in _MESSAGES.items():
        for language_code in SUPPORTED_LANGUAGES:
            for gender in voices.GENDERS:
                try:
                    # Through the system-speech cache: synthesised once and
                    # kept in S3, so a restart does not re-synthesise them.
                    _cache[
                        (stage, language_code, gender)
                    ] = await system_speech.get_system_speech(
                        text, language_code, gender=gender, purpose="tts_fallback"
                    )
                except Exception:
                    logger.error(
                        "Failed to pre-synthesize fallback audio for stage=%s "
                        "language=%s gender=%s",
                        stage.value,
                        language_code,
                        gender,
                        exc_info=True,
                    )
    logger.info("Pre-synthesized %d fallback audio clips", len(_cache))
