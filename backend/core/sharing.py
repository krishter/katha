"""
Her sharing preference (docs/specs/CONVERSATION_CARDS.md §5.1): whether any
of her stories may go to her child as cards, each still read to her first.

She can change it at any time by saying so. Each change is logged as a
consent record under her own principal, as evidence of what she said and
when. The version is "share-…", which core/parent_consent.py skips, so
nothing here can be mistaken for her answer to being recorded at all.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cards import state
from core.email import send_operator_alert
from core.parent_consent import PRINCIPAL_PARENT, SHARING_VERSION_PREFIX
from models.consent_record import ConsentRecord
from models.conversation_card import ConversationCard
from models.user_profile import UserProfileModel

logger = logging.getLogger(__name__)

SHARE_CONSENT_VERSION = f"{SHARING_VERSION_PREFIX}1.0"

NOT_ASKED = "not_asked"
SHARE = "share_with_approval"
UNSURE = "unsure"
NEVER = "never"
PREFERENCES = (NOT_ASKED, SHARE, UNSURE, NEVER)


async def set_preference(
    profile: UserProfileModel,
    preference: str,
    db: AsyncSession,
    *,
    turn_id: Optional[uuid.UUID] = None,
) -> bool:
    """
    Record her preference. Returns whether it changed. Commits.

    "never" also withdraws every card not yet sent: she has just said
    nothing is to go to her child, and that includes what is already on
    its way.
    """
    if preference not in PREFERENCES:
        raise ValueError(f"unknown share preference {preference!r}")
    if profile.share_preference == preference:
        return False

    previous = profile.share_preference
    profile.share_preference = preference
    if preference in (SHARE, NEVER):
        db.add(
            ConsentRecord(
                user_id=profile.user_id,
                email_hash="",
                consent_version=(
                    SHARE_CONSENT_VERSION
                    if preference == SHARE
                    else f"{SHARE_CONSENT_VERSION}-never"
                ),
                principal=PRINCIPAL_PARENT,
                evidence_ref=turn_id,
            )
        )

    withdrawn = 0
    if preference == NEVER:
        cards = (
            (
                await db.execute(
                    select(ConversationCard)
                    .where(ConversationCard.user_id == profile.user_id)
                    .where(ConversationCard.status.in_(state.WITHDRAWABLE))
                )
            )
            .scalars()
            .all()
        )
        for card in cards:
            withdrawn += state.withdraw(card)

    await db.commit()
    logger.info(
        "Share preference for %s: %s -> %s (turn %s, %d card(s) withdrawn)",
        profile.user_id,
        previous,
        preference,
        turn_id,
        withdrawn,
    )

    if preference == NEVER:
        # Q2: during the pilot a person talks to the family; nothing
        # automatic tells the child.
        await send_operator_alert(
            "A parent said not to share her stories",
            f"user_id {profile.user_id} set her sharing preference to never "
            f"(was {previous}). {withdrawn} card(s) withdrawn. Talk to the "
            "family; nothing has been sent to them.",
        )
    return True
