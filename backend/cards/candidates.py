"""
Whether a card may be drafted now, and from what (CONVERSATION_CARDS.md §7.1).

A draft needs all of:
- she said yes to sharing (share_preference = share_with_approval);
- no other card of hers in the pipeline (one at a time);
- room under D4: fewer than 2 cards approved, scheduled or sent in the
  last 7 days;
- at least one candidate atom.

A candidate is an atom with a verbatim quote, from a turn that is not
private and not on a private topic, that no earlier card has used. Atoms
of a card that failed its fidelity check go back into the pool once
(§7.4); after a second failure they stay out, so the same weak material
is not paid for session after session.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cards import state
from core import sharing
from models.conversation_card import ConversationCard
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel

CADENCE_WINDOW = timedelta(days=7)
CADENCE_LIMIT = 2  # D4
MAX_CANDIDATES = 30
_FIDELITY_RETRIES_PER_ATOM = 1

# Approved and on the way, or gone: these count towards D4.
_COUNTS_FOR_CADENCE = frozenset({"approved", "scheduled", "sent"})


async def cards_in_window(user_id: str, db: AsyncSession, now: datetime) -> int:
    """Cards approved, scheduled or sent whose moment falls in the last 7
    days: sent ones by when they went, the rest by when she approved."""
    since = now - CADENCE_WINDOW
    moment = func.coalesce(
        ConversationCard.email_sent_at,
        ConversationCard.scheduled_for,
        ConversationCard.parent_answered_at,
    )
    result = await db.execute(
        select(func.count(ConversationCard.id))
        .where(ConversationCard.user_id == user_id)
        .where(ConversationCard.status.in_(_COUNTS_FOR_CADENCE))
        .where(moment >= since)
    )
    return result.scalar_one()


async def blocked(
    profile: UserProfileModel, db: AsyncSession, now: Optional[datetime] = None
) -> Optional[str]:
    """Why no card may be drafted for her now, or None if one may."""
    now = now or datetime.now(timezone.utc)
    if profile.share_preference != sharing.SHARE:
        return f"share_preference is {profile.share_preference}"
    in_pipeline = await db.execute(
        select(func.count(ConversationCard.id))
        .where(ConversationCard.user_id == profile.user_id)
        .where(ConversationCard.status.in_(state.IN_PIPELINE))
    )
    if in_pipeline.scalar_one():
        return "a card is already in the pipeline"
    if await cards_in_window(profile.user_id, db, now) >= CADENCE_LIMIT:
        return f"{CADENCE_LIMIT} cards in the last 7 days"
    return None


async def candidate_atoms(
    profile: UserProfileModel, db: AsyncSession
) -> list[StoryAtom]:
    """Newest first, at most MAX_CANDIDATES."""
    used = Counter()
    rows = await db.execute(
        select(ConversationCard.status, ConversationCard.source_atom_ids).where(
            ConversationCard.user_id == profile.user_id
        )
    )
    blocked_ids = set()
    for status, atom_ids in rows.all():
        for atom_id in atom_ids or []:
            if status == "failed_fidelity":
                used[atom_id] += 1
            else:
                blocked_ids.add(atom_id)
    blocked_ids |= {
        atom_id for atom_id, n in used.items() if n > _FIDELITY_RETRIES_PER_ATOM
    }

    # Private topics are also given to the selection prompt: a turn on the
    # topic told in an earlier session, before she named it private, is
    # not marked, so only the selection call can keep it out.
    query = (
        select(StoryAtom)
        .join(Turn, Turn.id == StoryAtom.turn_id)
        .where(StoryAtom.user_id == profile.user_id)
        .where(StoryAtom.verbatim_quote.is_not(None))
        .where(func.length(func.trim(StoryAtom.verbatim_quote)) > 0)
        .where(Turn.is_private.is_(False))
        .order_by(StoryAtom.created_at.desc())
    )
    atoms = (await db.execute(query)).scalars().all()

    picked = []
    for atom in atoms:
        if atom.id in blocked_ids:
            continue
        picked.append(atom)
        if len(picked) >= MAX_CANDIDATES:
            break
    return picked
