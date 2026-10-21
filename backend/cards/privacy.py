"""
"Keep this between us" (docs/specs/CONVERSATION_CARDS.md §5.2), and her
sharing preference when she changes it in passing (§5.1).

Both arrive through the per-turn extraction pass. For every family (P5,
D5), cards on or off:

    share_restriction        none | this_turn | topic
    topic_label              what the topic is, for `topic`

and, when cards are on for her:

    share_preference_change  none | share | never

A private turn, and any atom drawn from it, never reaches anything the
family sees: a memory card, a conversation card, the dashboard or the
export. A card already on its way that drew on it is withdrawn; one
already sent is reported to the operator. A private topic is remembered on
her profile and reaches back over every session, and forward to any later
turn on it.
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
_TOPIC_BATCH = 50
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


async def _match_in_batches(
    label: str, turns: list[tuple[uuid.UUID, str]]
) -> set[uuid.UUID]:
    """match_topic_turns, _TOPIC_BATCH turns at a time: a topic now reaches
    back over every session she has had (P5), which can be hundreds of
    turns. Each batch fails closed on its own."""
    found: set[uuid.UUID] = set()
    for start in range(0, len(turns), _TOPIC_BATCH):
        found |= await match_topic_turns(label, turns[start : start + _TOPIC_BATCH])
    return found


async def _alert_if_already_sent(
    user_id: str, private_ids: set[uuid.UUID], db: AsyncSession
) -> None:
    """A card that already went out cannot be unsent. Say so, loudly: the
    dashboard hides it from now on, but the child may already have read
    it, and that is a conversation for a person to have."""
    from core.email import send_operator_alert
    from models.memory_card import MemoryCard
    from models.story_atom import StoryAtom

    if not private_ids:
        return
    memory_cards = (
        (
            await db.execute(
                select(MemoryCard.id)
                .join(StoryAtom, StoryAtom.id == MemoryCard.story_atom_id)
                .where(MemoryCard.user_id == user_id)
                .where(MemoryCard.twilio_message_sid.is_not(None))
                .where(StoryAtom.turn_id.in_(list(private_ids)))
            )
        )
        .scalars()
        .all()
    )
    conversation_cards = (
        (
            await db.execute(
                select(ConversationCard.id)
                .where(ConversationCard.user_id == user_id)
                .where(ConversationCard.status == "sent")
                .where(ConversationCard.source_turn_ids.overlap(list(private_ids)))
            )
        )
        .scalars()
        .all()
    )
    if not memory_cards and not conversation_cards:
        return
    logger.warning(
        "Already sent before %s asked for privacy: memory cards %s, "
        "conversation cards %s",
        user_id,
        memory_cards,
        conversation_cards,
    )
    await send_operator_alert(
        "A card was sent before she asked for privacy",
        f"{user_id} asked for something to stay private after a card drawing "
        "on it had already been sent to the family.\n"
        f"Memory cards: {[str(i) for i in memory_cards]}\n"
        f"Conversation cards: {[str(i) for i in conversation_cards]}\n"
        "The dashboard no longer shows them. Consider a gentle word with the "
        "family.",
    )


async def apply_private_request(
    extraction: dict,
    *,
    turn_id: uuid.UUID,
    user_id: str,
    db: AsyncSession,
    cards_on: bool,
) -> set[uuid.UUID]:
    """
    "Keep this between us", for every family (P5, D5). Marks this turn
    private, or every turn of hers on the topic across all her sessions,
    and remembers the topic on her profile. A turn on a topic she already
    asked to keep private is marked too, whether or not she says so again.
    With cards on, unsent cards drawing on those turns are withdrawn.
    Returns the turns marked private. Commits.
    """
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
    known_topics = list(profile.private_topics or []) if profile is not None else []

    private_ids: set[uuid.UUID] = set()
    topic_of: dict[uuid.UUID, str] = {}
    if restriction in ("this_turn", "topic"):
        private_ids = {turn_id}
        if restriction == "topic":
            earlier = (
                (
                    await db.execute(
                        select(Turn)
                        .where(Turn.user_id == user_id)
                        .where(Turn.id != turn_id)
                        .where(Turn.is_private.is_(False))
                        .order_by(Turn.created_at)
                    )
                )
                .scalars()
                .all()
            )
            private_ids |= await _match_in_batches(
                label, [(t.id, t.transcript) for t in earlier]
            )
            topic_of = {turn: label for turn in private_ids}
            if profile is not None and label not in known_topics:
                profile.private_topics = [*known_topics, label]
    elif known_topics:
        this_turn = (
            await db.execute(select(Turn).where(Turn.id == turn_id))
        ).scalar_one_or_none()
        if this_turn is not None and not this_turn.is_private:
            for topic in known_topics:
                if turn_id in await match_topic_turns(
                    topic, [(turn_id, this_turn.transcript)]
                ):
                    private_ids, topic_of = {turn_id}, {turn_id: topic}
                    break

    if private_ids:
        turns = (
            (await db.execute(select(Turn).where(Turn.id.in_(private_ids))))
            .scalars()
            .all()
        )
        for turn in turns:
            turn.is_private = True
            if turn.id in topic_of:
                turn.private_topic = topic_of[turn.id]
        if cards_on:
            await withdraw_cards_using(user_id, private_ids, db)
        await _alert_if_already_sent(user_id, private_ids, db)
        logger.info(
            "Marked %d turn(s) private for %s (%s%s)",
            len(turns),
            user_id,
            restriction,
            f": {label}" if restriction == "topic" else "",
        )
    await db.commit()
    return private_ids


async def apply_share_signals(
    extraction: dict,
    *,
    turn_id: uuid.UUID,
    session_id: str,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Act on this turn's share_restriction and share_preference_change,
    for a family with cards. Commits."""
    await apply_private_request(
        extraction, turn_id=turn_id, user_id=user_id, db=db, cards_on=True
    )
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one_or_none()
    change = PREFERENCE_CHANGES.get(
        str(extraction.get("share_preference_change") or "none")
    )
    if change and profile is not None:
        await sharing.set_preference(profile, change, db, turn_id=turn_id)


def not_private_atom():
    """
    A WHERE clause keeping story atoms the family may see: from a turn that
    is not private and whose extraction is done (P5: a turn whose privacy
    has not been read yet, or failed to be, stays hidden). NOT EXISTS, not
    NOT IN: an atom with no turn must stay visible, and `NULL NOT IN (...)`
    is not true.
    """
    from sqlalchemy import exists, or_

    from models.story_atom import StoryAtom

    return ~exists().where(
        Turn.id == StoryAtom.turn_id,
        or_(Turn.is_private.is_(True), Turn.extraction_status != "done"),
    )


def not_private_memory_card():
    """The same rule for memory cards, through the atom each was made from."""
    from sqlalchemy import exists, or_

    from models.memory_card import MemoryCard
    from models.story_atom import StoryAtom

    return ~exists().where(
        StoryAtom.id == MemoryCard.story_atom_id,
        Turn.id == StoryAtom.turn_id,
        or_(Turn.is_private.is_(True), Turn.extraction_status != "done"),
    )
