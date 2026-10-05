import asyncio
import logging
from dataclasses import dataclass

import httpx

from adapters.retry import post_with_retry
from config import settings
from media import audio_split

logger = logging.getLogger(__name__)

_SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"


@dataclass
class TranscriptResult:
    transcript: str
    language_code: str
    language_probability: float


# Sarvam transcribes at most 30s per synchronous call, and the PRD expects
# 3-5 minute voice notes, so most real turns need more than one call. Four
# at a time keeps a long note fast without opening dozens of connections
# to one vendor on a single turn.
_MAX_CONCURRENT_CHUNKS = 4


async def _transcribe_one(
    client: httpx.AsyncClient, audio_bytes: bytes, filename: str
) -> TranscriptResult:
    """One call to Sarvam. Raises on any non-200."""
    response = await post_with_retry(
        client,
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


async def transcribe(
    audio_bytes: bytes, filename: str = "audio.ogg"
) -> TranscriptResult:
    """
    Transcribe a voice note of any length.

    Anything over Sarvam's 30-second synchronous limit is split at natural
    pauses (see media.audio_split) and transcribed in parallel, then joined.
    A note short enough to send in one call takes exactly the path it always
    did — no ffmpeg, no re-encoding.

    The reported language is the chunk Sarvam was most confident about.
    Taking the first chunk's would let an opening "Haan..." decide the
    language for a five-minute answer, and the language chosen here is what
    the reply is spoken back in.
    """
    pieces = await audio_split.split_for_stt(audio_bytes)

    async with httpx.AsyncClient(timeout=60.0) as client:
        if len(pieces) == 1:
            return await _transcribe_one(client, pieces[0], filename)

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CHUNKS)

        async def _bounded(index: int, piece: bytes) -> TranscriptResult:
            async with semaphore:
                return await _transcribe_one(client, piece, f"part{index:03d}.ogg")

        results = await asyncio.gather(
            *(_bounded(i, piece) for i, piece in enumerate(pieces))
        )

    # Every chunk has to land. A silently dropped middle is worse than a
    # failed turn: she would be told her story was heard, and a hole in it
    # is not visible to anyone until the family reads the archive.
    best = max(results, key=lambda r: r.language_probability)
    transcript = " ".join(r.transcript.strip() for r in results if r.transcript.strip())

    logger.info(
        "Joined %d chunk transcripts (%d chars); language=%s (p=%.2f)",
        len(results),
        len(transcript),
        best.language_code,
        best.language_probability,
    )
    return TranscriptResult(
        transcript=transcript,
        language_code=best.language_code,
        language_probability=best.language_probability,
    )
