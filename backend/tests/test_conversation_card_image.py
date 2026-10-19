"""The "Did you know?" image (CONVERSATION_CARDS.md §7.5)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from memory_cards.generator import CardTooLong, render_conversation_card

CARD = dict(
    hook="Did you know Amma walked six kilometres to see her first film?",
    excerpt="We walked all the way to the talkies and told nobody.",
    call_prompt="What do you remember about that first film?",
    parent_title="Amma",
    language_name="Tamil",
    original_quote="யாருக்கும் சொல்லல",
    original_quote_gloss="I didn't tell anyone",
)


def test_it_is_a_4_by_5_png():
    image = Image.open(io.BytesIO(render_conversation_card(**CARD)))
    assert image.format == "PNG"
    assert image.size == (1080, 1350)


def test_the_longest_card_the_limits_allow_still_fits():
    """30-word hook, 60-word excerpt, 15-word prompt, a quote: shrunk to
    fit, never cut."""
    render_conversation_card(
        **{
            **CARD,
            "hook": "Did you know " + " ".join(["kilometres"] * 27),
            "excerpt": " ".join(["kilometres"] * 60),
            "call_prompt": " ".join(["remember"] * 15),
        }
    )


def test_text_that_cannot_fit_is_refused_not_truncated():
    with pytest.raises(CardTooLong):
        render_conversation_card(**{**CARD, "excerpt": "kilometres " * 400})


@pytest.mark.parametrize("language", ["Hindi", "Malayalam", "Urdu", "English"])
def test_every_script_renders(language):
    quotes = {
        "Hindi": "किसी को नहीं बताया",
        "Malayalam": "ആരോടും പറഞ്ഞില്ല",
        "Urdu": "کسی کو نہیں بتایا",
        "English": None,
    }
    render_conversation_card(
        **{
            **CARD,
            "language_name": language,
            "original_quote": quotes[language],
            "original_quote_gloss": "I told no one" if quotes[language] else None,
        }
    )
