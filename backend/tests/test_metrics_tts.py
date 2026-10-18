"""
The Gemini TTS figures in the admin metrics (docs/specs/GEMINI_TTS.md §6
stage 4): clips, audio minutes and fallbacks to Sarvam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from core import metrics, pricing
from core.metrics_page import _gemini_tts


def row(vendor, operation, model, units):
    return SimpleNamespace(
        vendor=vendor,
        operation=operation,
        model=model,
        units=units,
        created_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )


ROWS = [
    row(
        "google",
        "tts_reply",
        "gemini-3.8-flash-lite-tts",
        {"audio_seconds": 30.0, "input_characters": 400},
    ),
    row(
        "google",
        "tts_reply",
        "gemini-3.8-flash-lite-tts",
        {"audio_seconds": 60.0, "input_characters": 800},
    ),
    row(
        "google",
        "tts_opener",
        "gemini-3.8-flash-lite-tts",
        {"audio_seconds": 30.0, "input_characters": 300},
    ),
    row("sarvam", "tts_fallback_vendor", "bulbul:v3", {"characters": 300}),
    row("sarvam", "tts_reply", "bulbul:v4-flash", {"characters": 500}),
]


def test_gemini_clips_minutes_and_fallbacks_are_counted():
    units = metrics._units(ROWS)
    assert units["tts_gemini_clips"] == 3
    assert units["tts_gemini_audio_minutes"] == 2.0
    assert units["tts_vendor_fallbacks"] == 1
    assert units["tts_vendor_fallback_rate"] == 0.25
    # Sarvam's characters still include the fallback's.
    assert units["tts_characters"] == 800


def test_gemini_rows_are_priced_as_google_tts():
    parts = pricing.cost_breakdown(metrics._usage_row(ROWS[0]))
    assert set(parts) == {"google_tts"}
    assert parts["google_tts"] > Decimal(0)


def test_the_page_shows_the_fallback_rate_against_its_target():
    html = _gemini_tts(metrics._units(ROWS))
    assert "<b>3</b> clips" in html
    assert "<b>1</b>" in html and "keep under 1%" in html
