from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.story_atom import StoryAtom
from prompts.domains import get_domain

logger = logging.getLogger(__name__)

_CARD_SIZE = 1080
_MARGIN = 80
_TEXT_WIDTH = _CARD_SIZE - 2 * _MARGIN

_BG_COLOR = "#FDF6EC"
_QUOTE_COLOR = "#2C2C2C"
_MUTED_COLOR = "#6B5B4E"
_WORDMARK_COLOR = "#C8956C"

_FONTS_DIR = __file__.rsplit("/", 1)[0] + "/fonts"

# Katha's users speak and code-mix across Indian scripts (see PRD 2.3 / TC-06),
# so quotes routinely contain Devanagari or Tamil text mid-sentence. A single
# Latin-only face like base Noto Serif renders those runs as tofu boxes, which
# would break the product's signature "see your parent's own words" moment.
# We resolve a font per word by its dominant Unicode script and fall back to
# the Latin face for everything else (English, digits, punctuation).
_FONT_FILES = {
    "latin": {"regular": "NotoSerif-Regular.ttf", "bold": "NotoSerif-Bold.ttf"},
    "devanagari": {
        "regular": "NotoSerifDevanagari-Regular.ttf",
        "bold": "NotoSerifDevanagari-Bold.ttf",
    },
    "tamil": {
        "regular": "NotoSerifTamil-Regular.ttf",
        "bold": "NotoSerifTamil-Bold.ttf",
    },
}

_QUOTE_FONT_SIZE_DEFAULT = 44
_QUOTE_FONT_SIZE_LONG = 36
_LONG_QUOTE_THRESHOLD = 120
_ATTRIBUTION_FONT_SIZE = 20
_DATE_FONT_SIZE = 18
_WORDMARK_FONT_SIZE = 22
_LINE_SPACING = 14
_SPACE_WIDTH_RATIO = 0.28  # fraction of font size used as inter-word gap


@dataclass
class MemoryCardResult:
    image_bytes: bytes
    verbatim_quote: str
    domain: str
    story_atom_id: Optional[str]


def select_best_quote(story_atoms: list[dict]) -> Optional[dict]:
    """
    Pick the atom with the highest completeness_score.
    Tiebreak: prefer atoms with a non-empty verbatim_quote.
    Returns None if story_atoms is empty.
    """
    if not story_atoms:
        return None

    def sort_key(atom: dict) -> tuple[int, int]:
        has_quote = 1 if atom.get("verbatim_quote") else 0
        return (atom.get("completeness_score", 0), has_quote)

    return max(story_atoms, key=sort_key)


def _quote_text(atom: dict) -> str:
    """Return the atom's verbatim_quote, falling back to a narrative excerpt."""
    quote = atom.get("verbatim_quote")
    if quote:
        return quote
    narrative = atom.get("narrative") or ""
    return narrative[:200]


def _script_for_char(ch: str) -> Optional[str]:
    """Return the script bucket for a character, or None if script-neutral
    (whitespace, digits, punctuation) — callers should ignore neutral chars
    when deciding a word's dominant script."""
    codepoint = ord(ch)
    if 0x0900 <= codepoint <= 0x097F:
        return "devanagari"
    if 0x0B80 <= codepoint <= 0x0BFF:
        return "tamil"
    if ch.isalpha():
        return "latin"
    return None


def _script_for_word(word: str) -> str:
    """Pick the dominant script for a word by counting script-bearing chars."""
    counts: dict[str, int] = {}
    for ch in word:
        script = _script_for_char(ch)
        if script:
            counts[script] = counts.get(script, 0) + 1
    if not counts:
        return "latin"
    return max(counts, key=lambda s: counts[s])


@lru_cache(maxsize=32)
def _load_font(script: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
    filename = _FONT_FILES.get(script, _FONT_FILES["latin"])[weight]
    return ImageFont.truetype(f"{_FONTS_DIR}/{filename}", size)


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _layout_words(
    draw: ImageDraw.ImageDraw, text: str, weight: str, size: int
) -> list[tuple[str, ImageFont.FreeTypeFont, int]]:
    """Split text into (word, font, width) tuples, one font per word."""
    words = text.split()
    layout = []
    for word in words:
        font = _load_font(_script_for_word(word), weight, size)
        layout.append((word, font, _measure(draw, word, font)))
    return layout


def _wrap_words(
    words: list[tuple[str, ImageFont.FreeTypeFont, int]],
    max_width: int,
    space_width: int,
) -> list[list[tuple[str, ImageFont.FreeTypeFont, int]]]:
    if not words:
        return []

    lines: list[list[tuple[str, ImageFont.FreeTypeFont, int]]] = []
    current: list[tuple[str, ImageFont.FreeTypeFont, int]] = [words[0]]
    current_width = words[0][2]
    for word_entry in words[1:]:
        candidate_width = current_width + space_width + word_entry[2]
        if candidate_width <= max_width:
            current.append(word_entry)
            current_width = candidate_width
        else:
            lines.append(current)
            current = [word_entry]
            current_width = word_entry[2]
    lines.append(current)
    return lines


def _draw_centered_line(
    draw: ImageDraw.ImageDraw,
    line: list[tuple[str, ImageFont.FreeTypeFont, int]],
    y: int,
    space_width: int,
    fill: str,
) -> None:
    total_width = sum(w for _, _, w in line) + space_width * (len(line) - 1)
    x = (_CARD_SIZE - total_width) // 2
    for word, font, width in line:
        draw.text((x, y), word, font=font, fill=fill)
        x += width + space_width


def render_card(
    quote: str,
    user_name: str,
    domain_label: str,
    session_date: date,
) -> bytes:
    """Generate a 1080x1080 PNG memory card using Pillow."""
    image = Image.new("RGB", (_CARD_SIZE, _CARD_SIZE), color=_BG_COLOR)
    draw = ImageDraw.Draw(image)

    quote_font_size = (
        _QUOTE_FONT_SIZE_LONG
        if len(quote) > _LONG_QUOTE_THRESHOLD
        else _QUOTE_FONT_SIZE_DEFAULT
    )
    space_width = int(quote_font_size * _SPACE_WIDTH_RATIO)

    quoted_text = f"“{quote}”"
    words = _layout_words(draw, quoted_text, "bold", quote_font_size)
    lines = _wrap_words(words, _TEXT_WIDTH, space_width)

    max_lines = 4
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last_word, last_font, _ = lines[-1][-1]
        truncated = last_word.rstrip("”") + "…”"
        lines[-1][-1] = (truncated, last_font, _measure(draw, truncated, last_font))

    line_height = quote_font_size + _LINE_SPACING
    quote_block_height = line_height * len(lines)

    # Center vertically with a slight upward bias.
    quote_top = (_CARD_SIZE - quote_block_height) // 2 - 40

    y = quote_top
    for line in lines:
        _draw_centered_line(draw, line, y, space_width, _QUOTE_COLOR)
        y += line_height

    separator_y = y + 30
    draw.line(
        [(_MARGIN, separator_y), (_CARD_SIZE - _MARGIN, separator_y)],
        fill=_MUTED_COLOR,
        width=1,
    )

    attribution_font = _load_font(
        _script_for_word(user_name), "regular", _ATTRIBUTION_FONT_SIZE
    )
    attribution = f"{user_name} · {domain_label}"
    draw.text(
        (_MARGIN, separator_y + 24),
        attribution,
        font=attribution_font,
        fill=_MUTED_COLOR,
    )

    date_font = _load_font("latin", "regular", _DATE_FONT_SIZE)
    draw.text(
        (_MARGIN, separator_y + 24 + _ATTRIBUTION_FONT_SIZE + 10),
        session_date.strftime("%d %B %Y"),
        font=date_font,
        fill=_MUTED_COLOR,
    )

    wordmark_font = _load_font("latin", "regular", _WORDMARK_FONT_SIZE)
    wordmark = "katha"
    wordmark_width = _measure(draw, wordmark, wordmark_font)
    draw.text(
        (
            _CARD_SIZE - _MARGIN - wordmark_width,
            _CARD_SIZE - _MARGIN - _WORDMARK_FONT_SIZE,
        ),
        wordmark,
        font=wordmark_font,
        fill=_WORDMARK_COLOR,
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def generate_memory_card(
    session_id: str,
    user_id: str,
    user_name: str,
    db: AsyncSession,
) -> Optional[MemoryCardResult]:
    """
    Main entry point called post-session.
    Selects the best story atom from the session and renders a memory card.
    Returns None if the session has no story atoms with usable quotes.
    """
    result = await db.execute(
        select(StoryAtom)
        .where(StoryAtom.session_id == uuid.UUID(session_id))
        .order_by(StoryAtom.completeness_score.desc())
    )
    rows = result.scalars().all()
    if not rows:
        logger.warning(
            "No story atoms for session %s — no memory card generated", session_id
        )
        return None

    atoms = [
        {
            "id": str(row.id),
            "completeness_score": row.completeness_score,
            "verbatim_quote": row.verbatim_quote,
            "narrative": row.narrative,
            "domain": row.domain,
        }
        for row in rows
    ]

    best = select_best_quote(atoms)
    if best is None:
        logger.warning(
            "No usable quote for session %s — no memory card generated", session_id
        )
        return None

    quote = _quote_text(best)
    if not quote:
        logger.warning(
            "Selected atom has no quote or narrative for session %s", session_id
        )
        return None

    domain_label = get_domain(best["domain"]).name
    image_bytes = render_card(
        quote=quote,
        user_name=user_name,
        domain_label=domain_label,
        session_date=date.today(),
    )

    return MemoryCardResult(
        image_bytes=image_bytes,
        verbatim_quote=quote,
        domain=best["domain"],
        story_atom_id=best["id"],
    )
