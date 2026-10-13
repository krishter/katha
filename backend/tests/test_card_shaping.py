"""
Memory card text rendering for the scripts Katha is spoken in.

Two separate things have to be right, and the first one hid the second for
months:

1. The right FONT must be chosen. A script with no entry fell through
   `isalpha()` to the Latin face, which has no glyph for it, so the quote
   rendered as tofu boxes while the card around it looked perfect.

2. The text must be SHAPED. Indic scripts need the font's GSUB/GPOS tables
   applied — conjuncts formed, pre-base vowel signs moved in front of the
   consonant they follow in memory. Pillow only does that when built
   against libraqm, and the prebuilt wheels are not.

Devanagari is why this went unnoticed: Noto's i-matra glyph overhangs to
the left, so simple Hindi LOOKS shaped when it is not. The reorder test
below is the check that cannot be fooled by that.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFont

from memory_cards.generator import (
    _FONT_FILES,
    _FONTS_DIR,
    _HAS_RAQM,
    _LAYOUT,
    _load_font,
    _script_for_word,
)
from prompts.system_prompt import SUPPORTED_LANGUAGES

# One real word per supported language, with the script bucket it must
# resolve to. Keyed by the BCP-47 codes the product actually offers.
_SAMPLES: dict[str, tuple[str, str]] = {
    "hi-IN": ("हमारा घर", "devanagari"),
    "mr-IN": ("आमचं घर", "devanagari"),
    "ta-IN": ("எங்கள் வீடு", "tamil"),
    "te-IN": ("మా ఇల్లు", "telugu"),
    "kn-IN": ("ನಮ್ಮ ಮನೆ", "kannada"),
    "ml-IN": ("ഞങ്ങളുടെ വീട്", "malayalam"),
    "bn-IN": ("আমাদের বাড়ি", "bengali"),
    # Assamese is written in the Bengali script, with two letters of its
    # own (ৰ, ৱ) that Noto Serif Bengali covers. The sample uses one of
    # them deliberately.
    "as-IN": ("আমাৰ ঘৰ", "bengali"),
    "gu-IN": ("અમારું ઘર", "gujarati"),
    "pa-IN": ("ਸਾਡਾ ਘਰ", "gurmukhi"),
    "or-IN": ("ଆମ ଘର", "oriya"),
    "ur-IN": ("ہمارا گھر", "arabic"),
    "en-IN": ("our house", "latin"),
}

requires_raqm = pytest.mark.skipif(
    not _HAS_RAQM,
    reason=(
        "Pillow here has no libraqm, so text is not shaped. The production "
        "image installs it (backend/Dockerfile) and CI builds Pillow from "
        "source; a plain wheel on a developer machine does not."
    ),
)


def test_every_supported_language_has_a_sample():
    """If a language is added to the product, it gets a row here — that is
    what stops a new one quietly rendering as tofu."""
    assert set(_SAMPLES) == set(SUPPORTED_LANGUAGES)


@pytest.mark.parametrize("code", sorted(_SAMPLES))
def test_each_language_resolves_to_a_script_with_a_font(code):
    text, expected = _SAMPLES[code]
    assert _script_for_word(text.split()[0]) == expected
    assert expected in _FONT_FILES


@pytest.mark.parametrize("code", sorted(_SAMPLES))
def test_no_language_falls_through_to_the_latin_face(code):
    """The specific regression: Malayalam letters are alphabetic, so they
    were confidently routed to a font with no glyph for them."""
    text, expected = _SAMPLES[code]
    if expected == "latin":
        return
    assert _script_for_word(text.split()[0]) != "latin"


@pytest.mark.parametrize("code", sorted(_SAMPLES))
def test_the_font_file_actually_loads(code):
    _, script = _SAMPLES[code]
    for weight in ("regular", "bold"):
        assert _load_font(script, weight, 40) is not None


def _render(text: str, script: str, layout=None) -> bytes:
    font = ImageFont.truetype(
        f"{_FONTS_DIR}/{_FONT_FILES[script]['regular']}",
        48,
        layout_engine=layout if layout is not None else _LAYOUT,
    )
    img = Image.new("L", (900, 120), 255)
    ImageDraw.Draw(img).text((10, 10), text, font=font, fill=0)
    return img.tobytes()


def test_the_raqm_layout_engine_is_selected_when_available():
    assert (_LAYOUT is ImageFont.Layout.RAQM) == _HAS_RAQM


# Strings whose correct rendering needs GSUB/GPOS: conjuncts, pre-base
# vowel signs, and a right-to-left script.
_SHAPING_CASES = [
    ("devanagari", "हिन्दी में लिखा"),
    ("devanagari", "क्षत्रिय श्री"),
    ("tamil", "கொடி தமிழ்"),
    ("malayalam", "ഞങ്ങളുടെ വീട്"),
    ("bengali", "আমাদের বাড়ি"),
    ("kannada", "ನಮ್ಮ ಮನೆ"),
    ("telugu", "మా ఇల్లు"),
    ("arabic", "ہمارا گھر"),
]


@requires_raqm
@pytest.mark.parametrize("script,text", _SHAPING_CASES)
def test_shaping_changes_what_is_drawn(script, text):
    """The honest check that shaping is applied: render one string through
    RAQM and through BASIC and require the pixels to differ.

    An earlier version compared "कि" against "िक" and expected a match, on
    the theory that reordering makes them identical. It does not — with
    shaping, "िक" is an orphan vowel sign and HarfBuzz marks it with a
    dotted circle, so the two differ either way and the test discriminated
    nothing. It gave the same answer with and without libraqm, which is
    the definition of a useless check. Comparing the two layout engines on
    one string cannot be fooled like that.
    """
    assert _render(text, script, ImageFont.Layout.RAQM) != _render(
        text, script, ImageFont.Layout.BASIC
    )


@requires_raqm
@pytest.mark.parametrize("code", sorted(_SAMPLES))
def test_text_renders_as_something_other_than_blank(code):
    """A font with no glyphs for the script draws nothing.

    Gated on raqm because of Urdu: Nastaliq is entirely contextual, so
    without shaping there are no standalone forms to draw and the render
    really is blank. That is not a bug in this test — it is the clearest
    demonstration that an unshaped build cannot serve these languages.
    """
    text, script = _SAMPLES[code]
    blank = Image.new("L", (900, 120), 255).tobytes()
    assert _render(text, script) != blank, f"{code} rendered nothing"


@requires_raqm
def test_a_conjunct_is_not_drawn_as_its_separate_letters():
    """क्ष is a conjunct ligature, not क + ष side by side."""
    assert _render("क्ष", "devanagari") != _render("क ष", "devanagari")
