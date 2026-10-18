"""
A sanity check on synthesised speech before a parent hears it.

In the v4 Flash blind test (2026-10-17) one clip of twenty came back as
sixty seconds of audio: half a second of speech, 51 seconds of silence,
then the last few seconds of the reply. A parent would have held the phone
to their ear for most of a minute and heard nothing.

So a clip is rejected when it holds a long silence anywhere, or when it is
far longer than its text could take to say. Both limits sit well clear of
anything a normal clip does. Measured on the other 49 Hindi and English
clips from that day, v3 and v4 Flash alike: no silence over 0.6 s, and
12.5-20.4 characters per second. The broken clip ran 51 s of silence and
3.7 characters per second. A good clip is never thrown away for being slow
or pausing for breath.

Gemini clips sit as clear of both (GEMINI_TTS.md §4.3, 50 clips from the
2026-10-18 listening tests, Flash and Flash-Lite in Hindi, English and
Malayalam): 8.7-19.2 characters per second, no pause over 1.3 s. Its
"unhurried" style is slower than Sarvam, not slow enough to come near
the limits, so they are unchanged.

Only 16-bit PCM WAV is inspected, which is what Sarvam returns. Anything
the check cannot read passes: the check exists to catch a known failure,
never to cost a reply.
"""

from __future__ import annotations

import array
import io
import logging
import math
import sys
import wave

logger = logging.getLogger(__name__)

# Longest silence a clip may hold. Normal clips stay under 0.6 s.
MAX_SILENCE_SECONDS = 3.0
# Below this many characters per second the clip is mostly not speech.
# Normal clips run 12.5-20.4; the broken one ran 3.7.
MIN_CHARS_PER_SECOND = 5.0
# Short texts ("Okay.") take a fixed minimum to say, so the rate check only
# applies once a clip is long enough for the rate to mean something.
MIN_SECONDS_FOR_RATE_CHECK = 8.0

_WINDOW_SECONDS = 0.05
# A window is silent when its level is below -40 dBFS, the same threshold
# the measurement used (ffmpeg silencedetect n=-40dB).
_SILENCE_RMS = 32768 * 10 ** (-40 / 20)
# Every 4th sample is plenty to measure a 50 ms window's level, and keeps a
# minute of audio to a few hundred thousand multiplications.
_STRIDE = 4


def _samples(wav_bytes: bytes) -> tuple[array.array, int] | None:
    """Mono 16-bit samples and the sample rate, or None if unreadable."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as wav:
            if wav.getsampwidth() != 2:
                return None
            channels = wav.getnchannels()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError):
        return None
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        samples = samples[::channels]
    return samples, rate


def longest_silence(samples: array.array, rate: int) -> float:
    """Seconds of the longest run of windows quieter than -40 dBFS."""
    window = max(1, int(rate * _WINDOW_SECONDS))
    longest = run = 0
    for start in range(0, len(samples), window):
        chunk = samples[start : start + window : _STRIDE]
        if not chunk:
            break
        rms = math.sqrt(sum(x * x for x in chunk) / len(chunk))
        if rms < _SILENCE_RMS:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest * window / rate


def speech_problem(wav_bytes: bytes, text: str) -> str | None:
    """
    Why this clip should not be sent, or None if it looks like speech.
    """
    parsed = _samples(wav_bytes)
    if parsed is None:
        logger.debug("TTS check: could not read the clip as 16-bit WAV; passing it")
        return None
    samples, rate = parsed
    if not samples or rate <= 0:
        return "empty audio"
    duration = len(samples) / rate

    silence = longest_silence(samples, rate)
    if silence > MAX_SILENCE_SECONDS:
        return f"{silence:.1f} s of silence in a {duration:.1f} s clip"

    if duration >= MIN_SECONDS_FOR_RATE_CHECK:
        rate_cps = len(text) / duration
        if rate_cps < MIN_CHARS_PER_SECOND:
            return (
                f"{duration:.1f} s for {len(text)} characters "
                f"({rate_cps:.1f}/s, expected at least {MIN_CHARS_PER_SECOND:.0f}/s)"
            )
    return None
