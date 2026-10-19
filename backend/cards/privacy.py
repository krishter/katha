"""
"Keep this between us" (docs/specs/CONVERSATION_CARDS.md §5.2), and her
sharing preference when she changes it in passing (§5.1).

Both arrive through the per-turn extraction pass, which gains three
fields when cards are on for her:

    share_restriction        none | this_turn | topic
    topic_label              what the topic is, for `topic`
    share_preference_change  none | share | never

A private turn, and any atom drawn from it, never feeds a card, and any
card already on its way that drew on it is withdrawn. A private topic is
remembered on her profile so a later card cannot pick it up from a
different session either.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters import llm
from adapters.llm import Message
from cards import state
from config import settings
from consolidation.pipeline import parse_json_object
from core import sharing
from models.conversation_card import ConversationCard
from models.turn import Turn
from models.user_profile import UserProfileModel

logger = logging.getLogger(__name__)

RESTRICTIONS = ("none", "this_turn", "topic")
PREFERENCE_CHANGES = {"share": sharing.SHARE, "never": sharing.NEVER}

_TOPIC_PURPOSE = "private_topic_match"
_TOPIC_MAX_TOKENS = 400
_MAX_TOPIC_LABEL = 120


def _clean_label(label: object) -> Optional[str]:
    text = str(label or "").strip()
    return text[:_MAX_TOPIC_LABEL] or None


async def match_topic_turns(
    label: str, turns: list[tuple[uuid.UUID, str]]
) -> set[uuid.UUID]:
    """Which of these turns talk about `label`. On any failure, all of
    them: wrongly marking a turn private costs one card's material;
    missing one breaks her trust."""
    if not turns:
        return set()
    rendered = "\n\n".join(
        f"[{index}] {transcript}" for index, (_, transcript) in enumerate(turns)
    )
    prompt = f"""An elderly person asked for a topic to stay private: \
"{label}". Which of the numbered things they said earlier touch that topic \
at all, even briefly? When unsure, include it.

{rendered}

Respond with ONLY this JSON object: {{"indexes": [numbers]}}"""
    try:
        response = await llm.chat(
            [Message(role="user", content=prompt)],
            max_tokens=_TOPIC_MAX_TOKENS,
            model=settings.LLM_CLASSIFIER_MODEL,
            purpose=_TOPIC_PURPOSE,
        )
        indexes = parse_json_object(response.content).get("indexes")
        if not isinstance(indexes, list):
            raise ValueError(f"no index list in {response.content!r}")
        return {
            turns[i][0] for i in indexes if isinstance(i, int) and 0 <= i < len(turns)
        }
    except Exception:
        logger.exception("Topic match failed for %r — marking every turn", label)
        return {turn_id for turn_id, _ in turns}


async def withdraw_cards_using(
    user_id: str, turn_ids: set[uuid.UUID], db: AsyncSession
) -> int:
    """Withdraw every card not yet sent that drew on any of these turns."""
    if not turn_ids:
        return 0
    cards = (
        (
            await db.execute(
                select(ConversationCard)
                .where(ConversationCard.user_id == user_id)
                .where(ConversationCard.status.in_(state.WITHDRAWABLE))
                .where(ConversationCard.source_turn_ids.overlap(list(turn_ids)))
            )
        )
        .scalars()
        .all()
    )
    withdrawn = sum(state.withdraw(card) for card in cards)
    if withdrawn:
        logger.info(
            "Withdrew %d card(s) for %s: a source turn became private",
            withdrawn,
            user_id,
        )
    return withdrawn


async def apply_share_signals(
    extraction: dict,
    *,
    turn_id: uuid.UUID,
    session_id: str,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Act on this turn's share_restriction and share_preference_change.
    Commits."""
    restriction = str(extraction.get("share_restriction") or "none")
    label = _clean_label(extraction.get("topic_label"))
    if restriction == "topic" and not label:
        # Asked for privacy without our knowing about what: at least this
        # turn is private.
        restriction = "this_turn"

    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one_or_none()

    if restriction in ("this_turn", "topic"):
        private_ids = {turn_id}
        if restriction == "topic":
            earlier = (
                (
                    await db.execute(
                        select(Turn)
                        .where(Turn.session_id == uuid.UUID(session_id))
                        .where(Turn.id != turn_id)
                        .where(Turn.is_private.is_(False))
                        .order_by(Turn.turn_number)
                    )
                )
                .scalars()
                .all()
            )
            private_ids |= await match_topic_turns(
                label, [(t.id, t.transcript) for t in earlier]
            )
            if profile is not None and label not in (profile.private_topics or []):
                profile.private_topics = [*(profile.private_topics or []), label]

        turns = (
            (await db.execute(select(Turn).where(Turn.id.in_(private_ids))))
            .scalars()
            .all()
        )
        for turn in turns:
            turn.is_private = True
            if restriction == "topic":
                turn.private_topic = label
        await withdraw_cards_using(user_id, private_ids, db)
        await db.commit()
        logger.info(
            "Marked %d turn(s) private for %s (%s%s)",
            len(turns),
            user_id,
            restriction,
            f": {label}" if restriction == "topic" else "",
        )

    change = PREFERENCE_CHANGES.get(
        str(extraction.get("share_preference_change") or "none")
    )
    if change and profile is not None:
        await sharing.set_preference(profile, change, db, turn_id=turn_id)


def not_private_atom():
    """
    A WHERE clause keeping story atoms whose source turn is not private,
    for everything the family reads (dashboard, export). NOT EXISTS, not
    NOT IN: an atom with no turn must stay visible, and `NULL NOT IN (...)`
    is not true.
    """
    from sqlalchemy import exists

    from models.story_atom import StoryAtom

    return ~exists().where(Turn.id == StoryAtom.turn_id, Turn.is_private.is_(True))
