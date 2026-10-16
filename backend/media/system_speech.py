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

These objects hold no user data (no names, nothing a parent said), so they
sit outside DPDP deletion under system/speech/.

The cache never costs a reply: if S3 cannot be read or written, the phrase
is synthesised live exactly as before.
"""

from __future__ import annotations

import hashlib
import logging

from adapters import sarvam_tts
from config import settings
from media import audio_convert, storage, voices

logger = logging.getLogger(__name__)

_memory: dict[str, bytes] = {}


def speech_key(text: str, language_code: str) -> str:
    """The S3 key for a phrase as the current TTS model and voice would say it."""
    model, voice = voices.voice_for(settings.TTS_MODEL, language_code)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"system/speech/{model}/{voice}/{language_code}/{digest}.ogg"


async def _s3_get(key: str) -> bytes | None:
    """Patched in tests (backend/conftest.py), so no test reaches real S3."""
    return await storage.download_media(key)


async def _s3_put(key: str, data: bytes) -> None:
    """Patched in tests alongside _s3_get."""
    await storage.upload_media(data, key, content_type="audio/ogg")


async def get_system_speech(
    text: str, language_code: str, *, purpose: str = "tts_system"
) -> bytes:
    """
    OGG audio of `text` in `language_code`, synthesised at most once.

    Raises only if a live synthesis is needed and fails, which is what a
    direct TTS call would do; callers already degrade to text on that.
    """
    key = speech_key(text, language_code)
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

    wav = await sarvam_tts.synthesize(
        text, language_code=language_code, purpose=purpose
    )
    ogg = await audio_convert.convert_wav_to_ogg(wav)
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
