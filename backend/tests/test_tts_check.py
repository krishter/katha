"""The speech check that stops a mostly-silent clip reaching a parent."""

import array
import io
import math
import wave

from media import tts_check

RATE = 22050


def make_wav(*segments: tuple[str, float]) -> bytes:
    """A 16-bit mono WAV of ("tone"|"silence", seconds) segments."""
    samples = array.array("h")
    for kind, seconds in segments:
        n = int(RATE * seconds)
        if kind == "tone":
            samples.extend(
                int(8000 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(n)
            )
        else:
            samples.extend([0] * n)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


TEXT = "x" * 200  # about 13 s of speech at a normal rate


def test_a_normal_clip_with_breathing_pauses_passes():
    wav = make_wav(
        ("tone", 4), ("silence", 0.6), ("tone", 4), ("silence", 0.5), ("tone", 4)
    )
    assert tts_check.speech_problem(wav, TEXT) is None


def test_the_observed_failure_is_rejected():
    # Half a second of speech, 51 s of silence, the last 8 s of the reply.
    wav = make_wav(("tone", 0.5), ("silence", 51), ("tone", 8))
    problem = tts_check.speech_problem(wav, TEXT)
    assert problem and "silence" in problem


def test_a_long_silence_anywhere_is_rejected():
    wav = make_wav(("tone", 5), ("silence", 3.5), ("tone", 5))
    assert "silence" in tts_check.speech_problem(wav, TEXT)


def test_a_clip_far_too_long_for_its_text_is_rejected():
    # 40 s of sound for 100 characters: 2.5 characters a second.
    wav = make_wav(*[("tone", 2), ("silence", 2)] * 10)
    problem = tts_check.speech_problem(wav, "x" * 100)
    assert problem and "characters" in problem


def test_a_short_text_said_slowly_passes():
    # "Okay." in two seconds is a low rate, but too short to judge.
    wav = make_wav(("tone", 2))
    assert tts_check.speech_problem(wav, "Okay.") is None


def test_unreadable_audio_passes_rather_than_costing_a_reply():
    assert tts_check.speech_problem(b"RIFF" + b"\x00" * 40, TEXT) is None


def test_empty_audio_is_rejected():
    assert tts_check.speech_problem(make_wav(), TEXT) == "empty audio"
