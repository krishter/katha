"""
Speech for Katha's fixed system phrases, synthesised once
(docs/specs/COST_TO_SERVE.md C6).

Text with no name in it — the failure apologies, "we already talked
today", the consent outcomes — sounds the same for every parent, so paying
Sarvam to synthesise it again on every use, and 39 clips again on every
restart, is waste. Each phrase is kept as OGG in S3 and in a process-wide
dict, keyed by model, voice, language and a hash of the text, and only
synthesised on a miss. Changing any of the four makes a new key, so a
reworded phrase or a voice change can never replay stale audio.

The voice is the parent's pinned one (media/tts.py), passed in by the
caller; without it, the default for a new parent with that language and
persona. Gemini clips get their own keys, since the model and speaker
differ, so two vendors never share a clip.

These objects hold no user data (no names, nothing a parent said), so they
sit outside DPDP deletion under system/speech/. The key includes the
voice, so the two personas never share a clip.

The cache never costs a reply: if S3 cannot be read or written, the phrase
is synthesised live exactly as before.
"""

from __future__ import annotations

import hashlib
import logging

from media import audio_convert, storage, tts, voices

logger = logging.getLogger(__name__)

_memory: dict[str, bytes] = {}


def speech_key(
    text: str,
    language_code: str,
    gender: str | None,
    voice: voices.Voice | None = None,
) -> str:
    """The S3 key for a phrase as `voice` (by default, a new parent's voice
    for this language and persona) would say it."""
    voice = voice or tts.default_voice(language_code, gender)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"system/speech/{voice.model}/{voice.speaker}/{language_code}/{digest}.ogg"


async def _s3_get(key: str) -> bytes | None:
    """Patched in tests (backend/conftest.py), so no test reaches real S3."""
    return await storage.download_media(key)


async def _s3_put(key: str, data: bytes) -> None:
    """Patched in tests alongside _s3_get."""
    await storage.upload_media(data, key, content_type="audio/ogg")


async def get_system_speech(
    text: str,
    language_code: str,
    *,
    gender: str | None,
    purpose: str = "tts_system",
    voice: voices.Voice | None = None,
) -> bytes:
    """
    OGG audio of `text` in `language_code`, in the persona's voice,
    synthesised at most once per voice.

    Raises only if a live synthesis is needed and fails, which is what a
    direct TTS call would do; callers already degrade to text on that.
    """
    voice = voice or tts.default_voice(language_code, gender)
    key = speech_key(text, language_code, gender, voice)
    if key in _memory:
        return _memory[key]

    try:
        stored = await _s3_get(key)
    except Exception:
        logger.warning("System speech: could not read %s from S3", key, exc_info=True)
        stored = None
    if stored:
        _memory[key] = stored
        return stored

    wav, spoke = await tts.speak(
        text, voice, language_code=language_code, gender=gender, purpose=purpose
    )
    ogg = await audio_convert.convert_wav_to_ogg(wav)
    if spoke != voice:
        # Gemini failed and Sarvam stood in. Fine for this once, but never
        # stored under the Gemini key, or the stand-in would be replayed for
        # good. The next use tries Gemini again.
        return ogg
    _memory[key] = ogg
    try:
        await _s3_put(key, ogg)
    except Exception:
        logger.warning(
            "System speech: could not store %s in S3 — kept in memory only",
            key,
            exc_info=True,
        )
    return ogg


def clear_memory() -> None:
    _memory.clear()
