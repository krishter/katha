from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from adapters.retry import post_with_retry
from config import settings
from core import usage
from media import audio_split

logger = logging.getLogger(__name__)

_SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
_STT_MODEL = "saaras:v3"


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
            "model": _STT_MODEL,
            "language_code": "unknown",
            "mode": "transcribe",
        },
    )

    if response.status_code != 200:
        # The status and Sarvam's error code, not its body: an error body
        # can echo the input (P7).
        raise RuntimeError(
            f"Sarvam STT error {response.status_code}: {_error_code(response)}"
        )

    body = response.json()
    return TranscriptResult(
        transcript=body["transcript"],
        language_code=body["language_code"],
        language_probability=body["language_probability"],
    )


async def _record(
    received: float | None, billed: float | None, *, chunks: int, purpose: str = "stt"
) -> None:
    """
    One ledger row per transcribed note. Recorded only once every chunk has
    come back, which is also the only case where the turn goes on to use
    it. `seconds_received` is the note as she sent it; `seconds_billed` is
    what went to Sarvam after long pauses were shortened (C7). A duration
    ffprobe could not read is left out, and the row then shows as unpriced.
    """

    def _r(value):
        return round(value, 2) if value is not None else None

    await usage.record(
        "sarvam",
        purpose,
        {
            "seconds_received": _r(received),
            "seconds_billed": _r(billed),
            "chunks": chunks,
        },
        model=_STT_MODEL,
    )


async def transcribe(
    audio_bytes: bytes,
    filename: str = "audio.ogg",
    *,
    trim_pauses: bool | None = None,
    purpose: str = "stt",
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
    received = None
    # trim_pauses and purpose exist for the operator listening kit, which
    # transcribes the same note with and without trimming, billed as eval.
    # Production passes neither.
    if settings.STT_TRIM_PAUSES if trim_pauses is None else trim_pauses:
        audio_bytes, received, _shortened = await audio_split.compress_pauses(
            audio_bytes, settings.STT_MAX_PAUSE_SECONDS
        )
    pieces, seconds = await audio_split.split_for_stt_measured(audio_bytes)
    if received is None:
        received = seconds

    async with httpx.AsyncClient(timeout=60.0) as client:
        if len(pieces) == 1:
            result = await _transcribe_one(client, pieces[0], filename)
            await _record(received, seconds, chunks=1, purpose=purpose)
            return result

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
    await _record(received, seconds, chunks=len(results), purpose=purpose)

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


def _error_code(response) -> str:
    """Sarvam's machine-readable error code, if the body has one."""
    try:
        error = response.json().get("error") or {}
        return str(error.get("code") or "no code")
    except Exception:
        return "unparsed body"
