"""
The one way Katha speaks (docs/specs/GEMINI_TTS.md §4.2).

Every voice note goes through `synthesize(text, profile, purpose=...)`.
It speaks in the voice pinned on the parent's profile
(user_profiles.tts_voice, "vendor/model/speaker"), so she hears the same
voice for life whatever the settings of the day say. The settings —
TTS_VENDOR, GEMINI_TTS_LANGUAGES, TTS_MODEL — only choose the voice a NEW
parent is pinned to (`default_voice`). A profile with no readable pin
falls back to that default, so nothing is ever silent for want of a pin.

When Gemini fails hard (an HTTP error or a network error after its
retries), the reply is said in the persona's Sarvam voice instead (D3),
billed under purpose `tts_fallback_vendor` so the admin metrics show how
often it happens. A different voice beats none. If Sarvam fails as well,
the error reaches the caller, which sends the reply as text, as before.
"""

from __future__ import annotations

import logging
from typing import Any

from adapters import gemini_tts, sarvam_tts
from config import settings
from media import voices

logger = logging.getLogger(__name__)

FALLBACK_PURPOSE = "tts_fallback_vendor"


def default_voice(language_code: str, gender: str | None) -> voices.Voice:
    """The voice a new parent with this language and persona is pinned to."""
    return voices.default_voice(
        language_code,
        gender,
        tts_vendor=settings.TTS_VENDOR,
        tts_model=settings.TTS_MODEL,
        gemini_languages=voices.parse_languages(settings.GEMINI_TTS_LANGUAGES),
    )


def voice_of(profile: Any) -> voices.Voice:
    """The parent's pinned voice, or the default if the pin is missing or
    no longer fits her language. `profile` is a user_profiles row or a
    prompts.system_prompt.UserProfile."""
    language = profile.preferred_language
    pinned = voices.from_pin(getattr(profile, "tts_voice", None), language)
    return pinned or default_voice(language, profile.katha_gender)


async def speak(
    text: str,
    voice: voices.Voice,
    *,
    language_code: str,
    gender: str | None,
    purpose: str,
) -> tuple[bytes, voices.Voice]:
    """Speak `text` in `voice`. Returns the WAV bytes and the voice that
    actually spoke, which is the Sarvam fallback if Gemini failed (D3).
    `gender` is the persona, for Sarvam's v3 rescue and that fallback."""
    if voice.vendor != voices.GOOGLE:
        wav = await sarvam_tts.synthesize(
            text, language_code, gender=gender, purpose=purpose, voice=voice
        )
        return wav, voice
    try:
        wav = await gemini_tts.synthesize(
            text,
            language_code,
            gender=gender,
            purpose=purpose,
            model=voice.model,
            voice=voice.speaker,
        )
        return wav, voice
    except Exception:
        fallback = voices.sarvam_counterpart(language_code, gender, settings.TTS_MODEL)
        logger.error(
            "Gemini TTS failed (%s, %s) — saying it in Sarvam's %s instead",
            voice.pin,
            language_code,
            fallback.speaker,
            exc_info=True,
        )
        wav = await sarvam_tts.synthesize(
            text,
            language_code,
            gender=gender,
            purpose=FALLBACK_PURPOSE,
            voice=fallback,
        )
        return wav, fallback


async def synthesize_voice(
    text: str,
    voice: voices.Voice,
    *,
    language_code: str,
    gender: str | None,
    purpose: str,
) -> bytes:
    """speak(), for callers that only need the audio."""
    wav, _ = await speak(
        text, voice, language_code=language_code, gender=gender, purpose=purpose
    )
    return wav


async def synthesize(text: str, profile: Any, *, purpose: str) -> bytes:
    """
    Speak `text` to this parent, in her pinned voice. Returns WAV bytes.

    `purpose` — tts_reply, tts_opener, tts_system or tts_fallback — becomes
    the operation on the call's api_usage row.
    """
    return await synthesize_voice(
        text,
        voice_of(profile),
        language_code=profile.preferred_language,
        gender=profile.katha_gender,
        purpose=purpose,
    )
