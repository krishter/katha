"""
Split a voice note into pieces Sarvam's synchronous STT will accept.

Sarvam `speech-to-text` rejects anything over 30 seconds:

    400 Audio duration exceeds the maximum limit of 30 seconds.
       Please use the batch API for longer audio files.

The PRD expects 3-5 minute voice notes (6.2) — an elderly person describing
a street, a kitchen, a childhood. So the cap is not an edge case, it is the
normal case, and before this every note over half a minute came back as
"I could not quite hear that. Can you send that again?" (observed in
production 2026-10-05 on an 89-second note).

Cuts are placed at natural pauses rather than on a fixed grid. A hard cut
lands mid-word roughly once per chunk, and this archive exists to preserve
what someone actually said — a clipped word is a small permanent hole in
it. Reflective speech pauses often (35 silences in that 89-second note), so
a usable boundary is almost always available; when one is not, the cut
falls back to the hard limit, which is still better than failing the turn.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Sarvam's documented limit is 30s. The headroom absorbs container-duration
# rounding — a segment ffmpeg reports as 29.9s can still be rejected.
MAX_SYNC_SECONDS = 28.0

# Below this, a chunk is too short to be worth its own API call, so a
# boundary is not placed here even if a silence is available.
_MIN_CHUNK_SECONDS = 8.0

_SILENCE_NOISE_DB = "-25dB"
_SILENCE_MIN_DURATION = "0.3"

_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


async def _run(*args: str, timeout: float = 60.0) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"{args[0]} timed out after {timeout}s") from None
    return proc.returncode, stdout, stderr


async def probe_duration(path: Path) -> float:
    """Duration in seconds. Raises if ffprobe cannot read the file."""
    code, stdout, stderr = await _run(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(path),
        timeout=20.0,
    )
    if code != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode()[:200]}")
    return float(stdout.decode().strip())


async def _silence_ends(path: Path) -> list[float]:
    """
    Timestamps where a silence ends, i.e. where speech resumes — the right
    place to start the next chunk. Best-effort: returns [] on failure, and
    the caller falls back to fixed cuts.
    """
    try:
        _code, _stdout, stderr = await _run(
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={_SILENCE_NOISE_DB}:d={_SILENCE_MIN_DURATION}",
            "-f",
            "null",
            "/dev/null",
        )
    except RuntimeError:
        logger.warning("silencedetect timed out — falling back to fixed cuts")
        return []
    # silencedetect writes to stderr regardless of exit status.
    text = stderr.decode("utf-8", "replace")
    return [float(m) for m in _SILENCE_END_RE.findall(text)]


def _cut_points(duration: float, silences: list[float]) -> list[float]:
    """
    Where to cut, in seconds. Walks forward taking the latest silence that
    falls inside the allowed window, so chunks are as long as permitted
    while still breaking at a pause.
    """
    cuts: list[float] = []
    position = 0.0
    while duration - position > MAX_SYNC_SECONDS:
        window_end = position + MAX_SYNC_SECONDS
        window_start = position + _MIN_CHUNK_SECONDS
        candidates = [s for s in silences if window_start <= s <= window_end]
        cut = max(candidates) if candidates else window_end
        if cut <= position:  # pathological; never loop forever
            cut = window_end
        cuts.append(round(cut, 3))
        position = cut
    return cuts


async def split_for_stt(
    audio_bytes: bytes, max_seconds: float = MAX_SYNC_SECONDS
) -> list[bytes]:
    """
    Return the audio as one or more pieces, each within `max_seconds`.

    Short audio is returned unchanged and untouched — the common case
    (a brief reply) must not pay for ffmpeg or risk re-encoding artefacts.
    If anything about the split fails, the original is returned as a single
    piece: the caller then gets Sarvam's own error, which is the behaviour
    before this module existed, rather than a new failure mode.
    """
    with tempfile.TemporaryDirectory(prefix="katha-split-") as tmp:
        directory = Path(tmp)
        source = directory / "in.ogg"
        source.write_bytes(audio_bytes)

        try:
            duration = await probe_duration(source)
        except Exception:
            logger.warning(
                "Could not probe duration — passing audio through", exc_info=True
            )
            return [audio_bytes]

        if duration <= max_seconds:
            return [audio_bytes]

        logger.info(
            "Voice note is %.1fs — splitting for STT (limit %.0fs)",
            duration,
            max_seconds,
        )

        silences = await _silence_ends(source)
        cuts = _cut_points(duration, silences)
        if not cuts:
            return [audio_bytes]

        code, _stdout, stderr = await _run(
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-f",
            "segment",
            "-segment_times",
            ",".join(str(c) for c in cuts),
            "-c:a",
            "libopus",
            "-reset_timestamps",
            "1",
            str(directory / "seg%03d.ogg"),
            timeout=120.0,
        )
        if code != 0:
            logger.warning(
                "ffmpeg split failed (%s) — passing audio through",
                stderr.decode()[:200],
            )
            return [audio_bytes]

        pieces = [p.read_bytes() for p in sorted(directory.glob("seg*.ogg"))]
        if not pieces:
            logger.warning("Split produced no segments — passing audio through")
            return [audio_bytes]

        logger.info(
            "Split %.1fs into %d piece(s) at %s",
            duration,
            len(pieces),
            "silence boundaries" if silences else "fixed intervals",
        )
        return pieces
