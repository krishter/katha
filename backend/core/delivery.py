"""
What actually happened to a message we sent.

Twilio's REST call returning a SID means Twilio accepted the message, not
that anyone received it. For WhatsApp the two come apart routinely: a
free-form message to someone with no open 24-hour window is accepted,
given a SID, and then rejected by Meta with 63016 some seconds later. This
module is where that later word is written down.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.memory_card import MemoryCard

logger = logging.getLogger(__name__)

DELIVERED_STATUSES = frozenset({"delivered", "read"})


async def record_card_delivery(
    message_sid: str,
    status: str,
    error_code: Optional[str],
    db: AsyncSession,
) -> bool:
    """
    Record a delivery status against the memory card it belongs to.

    Returns True if a card matched. Most status callbacks are for voice
    notes rather than cards, so no match is the normal case and not a
    problem — it is logged at debug, not warning.

    `delivered_at` is set only for a status that means the handset got it.
    Statuses arrive in order (queued, sent, delivered) but are not
    guaranteed to, so a late "sent" after a "delivered" must not unset the
    timestamp: once delivered, it stays delivered.
    """
    result = await db.execute(
        select(MemoryCard).where(MemoryCard.twilio_message_sid == message_sid)
    )
    card = result.scalar_one_or_none()
    if card is None:
        logger.debug("Status %s for %s matched no memory card", status, message_sid)
        return False

    card.delivery_status = status
    card.delivery_error_code = error_code
    if status in DELIVERED_STATUSES and card.delivered_at is None:
        card.delivered_at = datetime.now(timezone.utc)

    db.add(card)
    await db.commit()
    logger.info(
        "Memory card %s for user %s: %s%s",
        card.id,
        card.user_id,
        status,
        f" (ErrorCode={error_code})" if error_code else "",
    )
    return True
