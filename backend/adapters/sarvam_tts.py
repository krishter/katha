import base64
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

_SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
_MAX_TEXT_CHARS = 2500


async def synthesize(
    text: str,
    language_code: str,
    speaker: str = "ritu",
) -> bytes:
    """Convert text to speech using Sarvam Bulbul V3. Returns raw WAV bytes."""
    if len(text) > _MAX_TEXT_CHARS:
        logger.warning(
            "TTS input truncated from %d to %d chars", len(text), _MAX_TEXT_CHARS
        )
        text = text[:_MAX_TEXT_CHARS]

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _SARVAM_TTS_URL,
            headers={
                "api-subscription-key": settings.SARVAM_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "target_language_code": language_code,
                "model": "bulbul:v3",
                "speaker": speaker,
                "pace": 1.0,
                "speech_sample_rate": 22050,
                "output_audio_codec": "wav",
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"Sarvam TTS error {response.status_code}: {response.text}")

    body = response.json()
    return base64.b64decode(body["audios"][0])
