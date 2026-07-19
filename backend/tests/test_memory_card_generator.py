from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from memory_cards.generator import (
    MemoryCardResult,
    generate_memory_card,
    render_card,
    select_best_quote,
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _atom(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "completeness_score": 3,
        "verbatim_quote": "The shop smelled of oil and metal.",
        "narrative": "My father had a shop in Madurai selling brass vessels.",
        "domain": "childhood",
    }
    base.update(overrides)
    return base


# ── select_best_quote ────────────────────────────────────────────────────────


def test_select_best_quote_picks_highest_completeness_score():
    low = _atom(completeness_score=2)
    high = _atom(completeness_score=4)
    assert select_best_quote([low, high]) is high


def test_select_best_quote_tiebreaks_on_verbatim_quote():
    without_quote = _atom(completeness_score=3, verbatim_quote=None)
    with_quote = _atom(completeness_score=3, verbatim_quote="A real quote.")
    assert select_best_quote([without_quote, with_quote]) is with_quote


def test_select_best_quote_returns_none_for_empty_list():
    assert select_best_quote([]) is None


# ── render_card ───────────────────────────────────────────────────────────────


def test_render_card_returns_png_bytes():
    png_bytes = render_card(
        quote="The street always smelled of jasmine in the mornings.",
        user_name="Subramaniam",
        domain_label="Childhood & Home",
        session_date=date(2026, 7, 18),
    )
    assert isinstance(png_bytes, bytes)
    assert len(png_bytes) > 0
    assert png_bytes[:8] == _PNG_SIGNATURE


def test_render_card_does_not_raise_for_long_quote():
    long_quote = "A" + " very long memory" * 20  # well over 200 chars
    assert len(long_quote) > 200
    png_bytes = render_card(
        quote=long_quote,
        user_name="Subramaniam",
        domain_label="Career & Work Life",
        session_date=date.today(),
    )
    assert png_bytes[:8] == _PNG_SIGNATURE


def test_render_card_does_not_raise_for_tamil_quote():
    tamil_quote = "என் தந்தை மதுரையில் ஒரு சிறிய கடை வைத்திருந்தார்."
    png_bytes = render_card(
        quote=tamil_quote,
        user_name="சுப்ரமணியம்",
        domain_label="Family & Ancestors",
        session_date=date.today(),
    )
    assert png_bytes[:8] == _PNG_SIGNATURE


def test_render_card_does_not_raise_for_code_mixed_quote():
    mixed_quote = "Humara ghar bahut bada tha — हम कम से कम 15 लोग थे।"
    png_bytes = render_card(
        quote=mixed_quote,
        user_name="Subramaniam",
        domain_label="Family & Ancestors",
        session_date=date.today(),
    )
    assert png_bytes[:8] == _PNG_SIGNATURE


# ── generate_memory_card ─────────────────────────────────────────────────────


def _make_db(rows):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


def _make_row(**overrides):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.completeness_score = 3
    row.verbatim_quote = "The shop smelled of oil and metal."
    row.narrative = "My father had a shop in Madurai."
    row.domain = "childhood"
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


async def test_generate_memory_card_returns_result_for_session_with_atoms():
    row = _make_row()
    db = _make_db([row])

    result = await generate_memory_card(str(uuid.uuid4()), "user-1", "Subramaniam", db)

    assert isinstance(result, MemoryCardResult)
    assert result.verbatim_quote == row.verbatim_quote
    assert result.domain == "childhood"
    assert result.story_atom_id == str(row.id)
    assert result.image_bytes[:8] == _PNG_SIGNATURE


async def test_generate_memory_card_returns_none_when_no_atoms():
    db = _make_db([])

    result = await generate_memory_card(str(uuid.uuid4()), "user-1", "Subramaniam", db)

    assert result is None


async def test_generate_memory_card_picks_highest_completeness_atom():
    low = _make_row(completeness_score=1, verbatim_quote="Low score quote.")
    high = _make_row(completeness_score=4, verbatim_quote="High score quote.")
    db = _make_db([high, low])

    result = await generate_memory_card(str(uuid.uuid4()), "user-1", "Subramaniam", db)

    assert result.verbatim_quote == "High score quote."
