"""The card image (CONVERSATION_CARDS.md §7.5), rendered from the card's
current words and stored privately in S3, ap-south-1."""

from __future__ import annotations

import asyncio
import logging

from media import storage
from memory_cards.generator import render_conversation_card
from models.conversation_card import ConversationCard
from prompts.system_prompt import _language_name

logger = logging.getLogger(__name__)


def image_key(card: ConversationCard) -> str:
    # The fingerprint is in the key, so an image always shows the words it
    # was rendered from: an edit renders a new object rather than
    # overwriting one an operator may be looking at.
    return f"cards/conv/{card.id}-{(card.readback_hash or 'x')[:12]}.png"


async def render_and_store(card: ConversationCard, parent_title: str) -> str:
    """Render, upload, record the key on the card. Does not commit."""
    language = card.source_language or card.readback_language or "en-IN"
    png = await asyncio.to_thread(
        render_conversation_card,
        hook=card.hook,
        excerpt=card.excerpt,
        call_prompt=card.call_prompt,
        parent_title=parent_title,
        language_name=_language_name(language),
        original_quote=card.original_quote,
        original_quote_gloss=card.original_quote_gloss,
    )
    key = image_key(card)
    await storage.upload_media(png, key, content_type="image/png")
    previous, card.image_s3_key = card.image_s3_key, key
    if previous and previous != key:
        # The image of words the card no longer holds. Deleted here so
        # nothing is left in S3 that DELETE /user/{user_id} cannot find.
        try:
            await storage.delete_media(previous)
        except Exception:
            logger.exception("Could not delete replaced card image %s", previous)
    return key


def is_current(card: ConversationCard) -> bool:
    """The stored image shows the card's current words."""
    return bool(card.image_s3_key) and card.image_s3_key == image_key(card)


async def try_render(card: ConversationCard, parent_title: str) -> bool:
    """As render_and_store, but a failure is logged, not raised: the card
    still goes to review, and the image is made again before sending."""
    try:
        await render_and_store(card, parent_title)
        return True
    except Exception:
        logger.exception("Could not render the image for card %s", card.id)
        return False
