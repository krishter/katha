import logging
from dataclasses import dataclass

import httpx

from config import settings

logger = logging.getLogger(__name__)

_SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"


@dataclass
class TranscriptResult:
    transcript: str
    language_code: str
    language_probability: float


async def transcribe(
    audio_bytes: bytes, filename: str = "audio.ogg"
) -> TranscriptResult:
    """Send audio to Sarvam Saaras V3 and return transcript."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _SARVAM_STT_URL,
            headers={"api-subscription-key": settings.SARVAM_API_KEY},
            files={"file": (filename, audio_bytes)},
            data={
                "model": "saaras:v3",
                "language_code": "unknown",
                "mode": "transcribe",
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"Sarvam STT error {response.status_code}: {response.text}")

    body = response.json()
    return TranscriptResult(
        transcript=body["transcript"],
        language_code=body["language_code"],
        language_probability=body["language_probability"],
    )
