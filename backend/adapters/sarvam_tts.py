from __future__ import annotations

import base64
import logging

import httpx

from adapters.retry import post_with_retry
from config import settings
from core import usage
from media import voices

logger = logging.getLogger(__name__)

_SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
_MAX_TEXT_CHARS = 2500


def _language_field(model: str) -> str:
    """
    The request field that carries the language.

    Sarvam's docs name it `language_code`, and v4 Flash is sent that.
    v3 keeps `target_language_code`, which production has always sent;
    both models accepted both names when checked live on 2026-10-16, so
    this only keeps the rollback path byte-for-byte unchanged.
    """
    return "language_code" if model == voices.V4_FLASH_MODEL else "target_language_code"


async def synthesize(
    text: str,
    language_code: str,
    speaker: str | None = None,
    *,
    purpose: str,
    model: str | None = None,
) -> bytes:
    """
    Convert text to speech with Sarvam Bulbul. Returns raw WAV bytes.

    The model is TTS_MODEL (COST_TO_SERVE.md C6); the voice comes from
    media/voices.py unless `speaker` overrides it. A language with no v4
    Flash voice is synthesised on v3 for this call, and the ledger records
    the model actually used.

    `purpose` — tts_reply, tts_opener, tts_system or tts_fallback — becomes
    the operation on the call's api_usage row.

    `model` overrides TTS_MODEL for this call only. Production never passes
    it; the operator listening kit does, to synthesise the same text on v3
    and v4 Flash for a blind comparison.
    """
    model, voice = voices.voice_for(model or settings.TTS_MODEL, language_code)
    if speaker is not None:
        voice = speaker
    if len(text) > _MAX_TEXT_CHARS:
        logger.warning(
            "TTS input truncated from %d to %d chars", len(text), _MAX_TEXT_CHARS
        )
        text = text[:_MAX_TEXT_CHARS]

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
                _language_field(model): language_code,
                "model": model,
                "speaker": voice,
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
    # truncation above, not before it.
    await usage.record("sarvam", purpose, {"characters": len(text)}, model=model)
    return audio
