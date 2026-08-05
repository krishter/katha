from __future__ import annotations

import logging
from enum import Enum

from adapters import sarvam_tts
from media.audio_convert import convert_wav_to_ogg
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

# (stage, language_code) -> pre-synthesized OGG/Opus bytes. Populated once at
# startup by preload_fallback_audio — a live failure must never depend on a
# live TTS call to apologise for a failure (which may itself be TTS's).
_cache: dict[tuple[FailureStage, str], bytes] = {}


def get_fallback_text(stage: FailureStage) -> str:
    return _MESSAGES[stage]


def get_fallback_audio(stage: FailureStage, language_code: str) -> bytes | None:
    """Return pre-synthesized fallback audio for (stage, language_code),
    falling back to English, or None if neither was successfully cached."""
    return _cache.get((stage, language_code)) or _cache.get((stage, _DEFAULT_LANGUAGE))


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
            try:
                wav_bytes = await sarvam_tts.synthesize(
                    text, language_code=language_code
                )
                ogg_bytes = await convert_wav_to_ogg(wav_bytes)
                _cache[(stage, language_code)] = ogg_bytes
            except Exception:
                logger.error(
                    "Failed to pre-synthesize fallback audio for stage=%s language=%s",
                    stage.value,
                    language_code,
                    exc_info=True,
                )
    logger.info("Pre-synthesized %d fallback audio clips", len(_cache))
