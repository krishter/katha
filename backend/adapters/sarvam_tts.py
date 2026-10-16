from __future__ import annotations

import base64
import logging

import httpx

from adapters.retry import post_with_retry
from config import settings
from core import usage

logger = logging.getLogger(__name__)

_SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
_MAX_TEXT_CHARS = 2500
_TTS_MODEL = "bulbul:v3"


async def synthesize(
    text: str,
    language_code: str,
    speaker: str = "ritu",
    *,
    purpose: str,
) -> bytes:
    """
    Convert text to speech using Sarvam Bulbul V3. Returns raw WAV bytes.

    `purpose` — tts_reply, tts_opener, tts_system or tts_fallback — becomes
    the operation on the call's api_usage row.
    """
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
                "target_language_code": language_code,
                "model": _TTS_MODEL,
                "speaker": speaker,
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
    await usage.record("sarvam", purpose, {"characters": len(text)}, model=_TTS_MODEL)
    return audio
