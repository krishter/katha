from __future__ import annotations

import base64
import logging
import re

import httpx

from adapters.retry import post_with_retry
from config import settings
from core import usage
from media import tts_check, voices

logger = logging.getLogger(__name__)

_SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
_MAX_TEXT_CHARS = 2500

# Emoji and pictographs: a reply once carried a 😊 to the speech engine.
# The ranges cover emoji, dingbats, misc symbols and arrows; ₹ (U+20B9) and
# every Indic block lie outside them.
_PICTOGRAPH = "[\U0001f000-\U0001faff\u2600-\u27bf\u2300-\u23ff\u2b00-\u2bff]"
# A pictograph with its variation selectors, skin tones and any further
# pictographs it is joined to. ZWJ and ZWNJ are only removed inside such a
# sequence: on their own they shape Indic letters and must be kept.
_EMOJI = re.compile(
    _PICTOGRAPH
    + "(?:[\ufe0e\ufe0f\U0001f3fb-\U0001f3ff]|\u200d"
    + _PICTOGRAPH
    + ")*"
    + "|[\ufe0e\ufe0f]"
)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?।])")


def speakable(text: str) -> str:
    """`text` with emoji removed and the spacing they leave tidied."""
    if not _EMOJI.search(text):
        return text
    text = _EMOJI.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text).strip()


def _language_field(model: str) -> str:
    """
    The request field that carries the language.

    Sarvam's docs name it `language_code`, and v4 Flash is sent that.
    v3 keeps `target_language_code`, which production has always sent;
    both models accepted both names when checked live on 2026-10-16, so
    this only keeps the rollback path byte-for-byte unchanged.
    """
    return "language_code" if model == voices.V4_FLASH_MODEL else "target_language_code"


async def _request(text: str, voice: voices.Voice, purpose: str) -> bytes:
    """One billed call to Sarvam. Returns raw WAV bytes."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await post_with_retry(
            client,
            _SARVAM_TTS_URL,
            headers={
                "api-subscription-key": settings.SARVAM_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                _language_field(voice.model): voice.language_code,
                "model": voice.model,
                "speaker": voice.speaker,
                "pace": 1.0,
                "speech_sample_rate": 22050,
                "output_audio_codec": "wav",
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"Sarvam TTS error {response.status_code}: {response.text}")

    body = response.json()
    audio = base64.b64decode(body["audios"][0])
    # Sarvam bills by characters sent, so this counts the text after the
    # truncation in synthesize, not before it.
    await usage.record("sarvam", purpose, {"characters": len(text)}, model=voice.model)
    return audio


async def synthesize(
    text: str,
    language_code: str,
    *,
    gender: str | None,
    purpose: str,
    model: str | None = None,
) -> bytes:
    """
    Convert text to speech with Sarvam Bulbul. Returns raw WAV bytes.

    `language_code` is the parent's profile language and `gender` is
    Katha's persona ("female" or "male"); together they pick the model and
    voice in media/voices.py. Callers pass the profile's values, never the
    language detected in a voice note, so a parent always hears one voice.
    The ledger records the model actually used.

    `purpose` — tts_reply, tts_opener, tts_system or tts_fallback — becomes
    the operation on the call's api_usage row.

    `model` overrides TTS_MODEL for this call only. Production never passes
    it; the operator listening kit does, to synthesise the same text on v3
    and v4 Flash for a blind comparison.

    Emoji are removed first. Each clip then goes through
    media/tts_check.py: a clip that is mostly silence is synthesised again
    once, and if v4 Flash fails twice the reply is said on v3, in the same
    persona, instead. Every attempt is billed and recorded. A clip that
    still fails on the last attempt is returned anyway, with an error logged
    — a poor voice note is still better than none, and the caller's own
    fallbacks are for errors, not for this.
    """
    voice = voices.voice_for(language_code, gender, model or settings.TTS_MODEL)
    text = speakable(text)
    if len(text) > _MAX_TEXT_CHARS:
        logger.warning(
            "TTS input truncated from %d to %d chars", len(text), _MAX_TEXT_CHARS
        )
        text = text[:_MAX_TEXT_CHARS]

    attempts = [voice, voice]
    if voice.model == voices.V4_FLASH_MODEL:
        attempts.append(voices.v3_voice(language_code, gender))

    for number, attempt in enumerate(attempts, start=1):
        audio = await _request(text, attempt, purpose)
        problem = tts_check.speech_problem(audio, text)
        if problem is None:
            return audio
        last = number == len(attempts)
        log = logger.error if last else logger.warning
        log(
            "TTS clip rejected (%s %s, %s, attempt %d of %d): %s%s",
            attempt.model,
            attempt.speaker,
            language_code,
            number,
            len(attempts),
            problem,
            " — sending it anyway" if last else "",
        )
    return audio
