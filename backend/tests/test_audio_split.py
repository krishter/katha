"""
Splitting long voice notes for Sarvam's 30-second synchronous limit.

The ffmpeg-backed tests run the real binary on real generated audio. That
is deliberate: media/audio_split.py exists because of how an external tool
behaves at a boundary, and the bug it fixes (Sarvam rejecting an 89-second
note with 400, so the parent heard "I could not quite hear that") was
invisible to a suite that mocks every subprocess. test_audio_convert.py
still mocks create_subprocess_exec, so before this file ffmpeg had never
once been executed by the tests.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from media.audio_split import (
    MAX_SYNC_SECONDS,
    _cut_points,
    probe_duration,
    split_for_stt,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(_HAS_FFMPEG is False, reason="ffmpeg not installed")


# ── cut-point arithmetic (no ffmpeg needed) ──────────────────────────────────


def test_short_audio_needs_no_cuts():
    assert _cut_points(20.0, []) == []
    assert _cut_points(MAX_SYNC_SECONDS, []) == []


def test_long_audio_without_silences_cuts_at_the_limit():
    """No pauses to use, so chunks run right up to the cap."""
    cuts = _cut_points(100.0, [])
    assert cuts == [28.0, 56.0, 84.0]
    assert all(b - a <= MAX_SYNC_SECONDS for a, b in zip([0.0] + cuts, cuts))


def test_cuts_prefer_the_latest_silence_in_the_window():
    """
    A pause at 26s and one at 12s are both legal for the first chunk; the
    later one is chosen so chunks stay long and the call count stays low.
    """
    cuts = _cut_points(60.0, [12.0, 26.0, 50.0])
    assert cuts[0] == 26.0
    assert cuts[1] == 50.0


def test_silences_too_early_are_ignored():
    """A pause 2s in would make a chunk not worth its own API call."""
    cuts = _cut_points(60.0, [2.0, 3.5])
    assert cuts[0] == MAX_SYNC_SECONDS


def test_silences_beyond_the_limit_are_ignored():
    """A pause at 40s cannot anchor a chunk capped at 28s."""
    assert _cut_points(60.0, [40.0])[0] == MAX_SYNC_SECONDS


def test_every_chunk_is_within_the_limit_for_a_long_note():
    """A five-minute note — the PRD's expected length (6.2)."""
    cuts = _cut_points(300.0, [])
    bounds = [0.0] + cuts + [300.0]
    spans = [b - a for a, b in zip(bounds, bounds[1:])]
    assert all(s <= MAX_SYNC_SECONDS + 0.001 for s in spans), spans


# ── real ffmpeg ──────────────────────────────────────────────────────────────


def _make_audio(path: Path, spec: str) -> None:
    """Render an ffmpeg filter spec to an ogg/opus file."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            spec,
            "-c:a",
            "libopus",
            str(path),
        ],
        check=True,
    )


def _tone(seconds: float) -> str:
    return f"sine=frequency=300:duration={seconds}"


@needs_ffmpeg
async def test_audio_under_the_limit_is_returned_byte_identical():
    """The common case must not pay for ffmpeg or risk re-encoding."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "short.ogg"
        _make_audio(path, _tone(10))
        original = path.read_bytes()

    pieces = await split_for_stt(original)
    assert len(pieces) == 1
    assert pieces[0] is original or pieces[0] == original


@needs_ffmpeg
async def test_long_audio_is_split_and_every_piece_fits():
    """The actual regression: 90s in, and nothing handed to Sarvam over 30s."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "long.ogg"
        _make_audio(source, _tone(90))
        pieces = await split_for_stt(source.read_bytes())

        assert len(pieces) >= 4, f"90s should yield 4+ pieces, got {len(pieces)}"

        for index, piece in enumerate(pieces):
            part = Path(tmp) / f"p{index}.ogg"
            part.write_bytes(piece)
            duration = await probe_duration(part)
            assert duration <= 30.0, (
                f"piece {index} is {duration:.1f}s — Sarvam rejects over 30s, "
                "which is the whole point of this module"
            )


@needs_ffmpeg
async def test_no_audio_is_lost_across_the_split():
    """
    The pieces must sum to the original. A dropped middle would be worse
    than a failed turn: she would be told her story was heard, with a hole
    in it nobody notices until the family reads the archive.
    """
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "long.ogg"
        _make_audio(source, _tone(90))
        total_before = await probe_duration(source)

        pieces = await split_for_stt(source.read_bytes())
        total_after = 0.0
        for index, piece in enumerate(pieces):
            part = Path(tmp) / f"p{index}.ogg"
            part.write_bytes(piece)
            total_after += await probe_duration(part)

    assert total_after == pytest.approx(total_before, abs=1.0), (
        f"{total_before:.1f}s in, {total_after:.1f}s out — audio was lost"
    )


@needs_ffmpeg
async def test_cuts_land_on_a_pause_when_one_exists():
    """
    Tone, a 2s silence at 20s, then more tone. The cut should take the
    pause rather than slicing mid-tone at the 28s cap.
    """
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "paused.ogg"
        spec = (
            f"{_tone(20)}[a];anullsrc=duration=2[b];{_tone(40)}[c];"
            "[a][b][c]concat=n=3:v=0:a=1"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                spec,
                "-c:a",
                "libopus",
                str(source),
            ],
            check=True,
        )
        pieces = await split_for_stt(source.read_bytes())
        first = Path(tmp) / "first.ogg"
        first.write_bytes(pieces[0])
        first_duration = await probe_duration(first)

    # The pause spans 20-22s, so a silence-aware cut lands there, well
    # short of the 28s hard limit.
    assert 19.0 <= first_duration <= 24.0, (
        f"first piece is {first_duration:.1f}s — expected a cut at the "
        "20-22s pause, not at the 28s cap"
    )


@needs_ffmpeg
async def test_unreadable_audio_passes_through_rather_than_raising():
    """
    Garbage in means Sarvam's own error out — the behaviour before this
    module existed — not a new failure mode inside the split.
    """
    pieces = await split_for_stt(b"this is not audio")
    assert pieces == [b"this is not audio"]


def test_split_is_not_silently_skipped_in_ci():
    """
    If ffmpeg is missing, every test above skips and this file reports
    green while verifying nothing. CI installs ffmpeg; this makes its
    absence loud there rather than quiet.
    """
    import os

    # Keyed on CI alone. A developer without ffmpeg should still get a
    # useful local run; CI is where a silent skip would let this file
    # report green while verifying nothing.
    if not os.getenv("CI"):
        pytest.skip("local run — ffmpeg optional")

    assert _HAS_FFMPEG, (
        "ffmpeg is not installed, so every audio-split test skipped and this "
        "file verified nothing. CI installs it — see .github/workflows/ci.yml"
    )
