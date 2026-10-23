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


# ── Embeddings never count as TTS (SEMANTIC_RETRIEVAL.md §5.1, §5.8) ──────

_TAG = "gemini-embedding-001@1536"
EMBEDDING_ROWS = [
    row("google_embeddings", "embed_document", _TAG, {"tokens": 60}),
    row("google_embeddings", "embed_document", _TAG, {"tokens": 80}),
    row("google_embeddings", "embed_query", _TAG, {"tokens": 40}),
    row(
        "google_embeddings",
        "embed_query_fallback",
        "gemini-embedding-001@1536",
        {"reason": "timeout"},
    ),
]


def test_embedding_rows_leave_the_tts_figures_unchanged():
    tts_only = metrics._units(ROWS)
    mixed = metrics._units(ROWS + EMBEDDING_ROWS)
    for key in (
        "tts_gemini_clips",
        "tts_gemini_audio_minutes",
        "tts_vendor_fallbacks",
        "tts_vendor_fallback_rate",
        "tts_characters",
    ):
        assert mixed[key] == tts_only[key], key


def test_embedding_units_are_counted():
    u = metrics._units(ROWS + EMBEDDING_ROWS)
    assert u["embedding_tokens"] == 180
    assert u["embedding_document_calls"] == 2
    assert u["embedding_query_calls"] == 1
    assert u["embedding_query_fallbacks"] == 1
    assert u["embedding_query_fallback_rate"] == 0.5


def test_embedding_units_are_zero_with_no_rows():
    u = metrics._units(ROWS)
    assert u["embedding_tokens"] == 0
    assert u["embedding_document_calls"] == 0
    assert u["embedding_query_calls"] == 0
    assert u["embedding_query_fallbacks"] == 0
    assert u["embedding_query_fallback_rate"] is None


def test_embeddings_have_their_own_cost_component():
    parts = pricing.cost_breakdown(EMBEDDING_ROWS[0])
    assert set(parts) == {"google_embeddings"}
    assert "google_tts" not in parts
