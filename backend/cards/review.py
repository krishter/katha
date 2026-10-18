"""
The operator's review of a card before she hears it (CONVERSATION_CARDS.md
§7.6, D3), and of child questions screening held (§10.2).

    approve   operator_review -> awaiting_parent. Only a card whose last
              fidelity check passed and that has its image.
    edit      change any text field; the card is checked again (fidelity
              and length) and re-rendered. It stays in review for an
              explicit approve.
    reject    operator_review -> rejected, with a reason kept for prompt
              tuning.

None of these can send anything to the family: the furthest a card gets
from here is awaiting_parent, which only her yes moves on (cards/state.py).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cards import image as card_image
from cards import state, write
from cards.context import family_of
from config import settings
from models.conversation_card import ConversationCard
from models.family_question import FamilyQuestion
from models.user_profile import UserProfileModel

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = (
    "title",
    "hook",
    "excerpt",
    "original_quote",
    "original_quote_gloss",
    "call_prompt",
    "readback_text",
    "held_back_note",
)
CALL_TAGS = ("brought_up", "not_mentioned")


class ReviewError(ValueError):
    """An action the card's state does not allow; shown to the operator."""


def csrf_token() -> str:
    """A form token only someone holding ADMIN_API_TOKEN can produce.

    The page authenticates with HTTP Basic, which a browser re-sends on any
    request to the host, including a form another site submits. This token
    is what that other site cannot know."""
    return hmac.new(
        settings.ADMIN_API_TOKEN.encode("utf-8"), b"katha-cards-review", hashlib.sha256
    ).hexdigest()


def csrf_ok(presented: Optional[str]) -> bool:
    return bool(presented) and hmac.compare_digest(presented, csrf_token())


async def _card(card_id: uuid.UUID, db: AsyncSession) -> ConversationCard:
    card = (
        await db.execute(select(ConversationCard).where(ConversationCard.id == card_id))
    ).scalar_one_or_none()
    if card is None:
        raise ReviewError("No such card")
    return card


async def _parent_title(user_id: str, db: AsyncSession) -> str:
    family = await family_of(user_id, db)
    if family and family.parent_title:
        return family.parent_title
    profile = (
        await db.execute(
            select(UserProfileModel.name).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one_or_none()
    return profile or "your parent"


def _mark(card: ConversationCard, action: str, reason: Optional[str] = None) -> None:
    card.operator_action = action
    card.operator_reviewed_at = datetime.now(timezone.utc)
    if reason is not None:
        card.operator_reason = reason


async def approve(card_id: uuid.UUID, db: AsyncSession) -> ConversationCard:
    card = await _card(card_id, db)
    if card.status != "operator_review":
        raise ReviewError(f"This card is {card.status}, not waiting for review")
    if (card.fidelity_json or {}).get("result") != "passed":
        raise ReviewError("Its last fidelity check did not pass; edit it first")
    if not card.image_s3_key and not await card_image.try_render(
        card, await _parent_title(card.user_id, db)
    ):
        raise ReviewError("Its image could not be made; try again")
    state.transition(card, "awaiting_parent")
    _mark(card, "approve")
    await db.commit()
    logger.info("Operator approved card %s for %s", card.id, card.user_id)
    return card


async def reject(card_id: uuid.UUID, reason: str, db: AsyncSession) -> ConversationCard:
    card = await _card(card_id, db)
    if card.status != "operator_review":
        raise ReviewError(f"This card is {card.status}, not waiting for review")
    if not reason.strip():
        raise ReviewError("Give a reason: it is kept for tuning the prompts")
    state.transition(card, "rejected")
    _mark(card, "reject", reason.strip())
    await db.commit()
    logger.info("Operator rejected card %s: %s", card.id, reason.strip())
    return card


async def edit(card_id: uuid.UUID, fields: dict, db: AsyncSession) -> ConversationCard:
    """Change the card's words, check them again, re-render. Commits."""
    card = await _card(card_id, db)
    if card.status != "operator_review":
        raise ReviewError(f"This card is {card.status}; it can no longer be edited")
    changes = {
        name: (str(value).strip() or None)
        for name, value in fields.items()
        if name in EDITABLE_FIELDS
    }
    for required in ("title", "hook", "excerpt", "call_prompt", "readback_text"):
        if required in changes:
            changes[required] = changes[required] or ""
    if "held_back_note" in changes:
        changes["held_back_note"] = changes["held_back_note"] or ""
    current = {name: getattr(card, name) for name in EDITABLE_FIELDS}
    changes = {k: v for k, v in changes.items() if v != current[k]}
    if not changes:
        return card

    state.set_text(card, **changes)
    edited = {name: getattr(card, name) for name in EDITABLE_FIELDS}
    parent_title = await _parent_title(card.user_id, db)
    turns = await write.load_turns(list(card.source_turn_ids or []), db)

    try:
        sentences, problems = await write.check_fidelity(edited, turns, parent_title)
    except Exception:
        logger.exception("Fidelity check failed for edited card %s", card.id)
        sentences, problems = [], ["the fidelity check could not run; try again"]
    problems = write.shape_problems(edited) + problems
    history = list((card.fidelity_json or {}).get("passes") or [])
    history.append(
        {
            "fields": edited,
            "sentences": sentences,
            "problems": problems,
            "by": "operator",
        }
    )
    card.fidelity_json = {
        **(card.fidelity_json or {}),
        "result": "failed" if problems else "passed",
        "sentences": sentences,
        "passes": history,
    }
    card.operator_edited = True
    card.image_s3_key = None
    if not problems:
        await card_image.try_render(card, parent_title)
    _mark(card, "edit")
    await db.commit()
    return card


async def tag_call(card_id: uuid.UUID, tag: str, db: AsyncSession) -> ConversationCard:
    """§13: she brought up, unprompted, a call with her child about this card."""
    if tag not in CALL_TAGS:
        raise ReviewError(f"Tag must be one of {', '.join(CALL_TAGS)}")
    card = await _card(card_id, db)
    if card.status != "sent":
        raise ReviewError("Only a sent card can be tagged")
    card.call_tag = tag
    await db.commit()
    return card


async def resolve_question(
    question_id: uuid.UUID, release: bool, db: AsyncSession
) -> FamilyQuestion:
    """A question screening held: release it to be asked, or drop it."""
    question = (
        await db.execute(select(FamilyQuestion).where(FamilyQuestion.id == question_id))
    ).scalar_one_or_none()
    if question is None:
        raise ReviewError("No such question")
    if question.status != "held":
        raise ReviewError(f"This question is {question.status}, not held")
    question.status = "released" if release else "dropped"
    await db.commit()
    logger.info("Operator %s question %s", question.status, question.id)
    return question


async def review_queue(db: AsyncSession) -> dict:
    """Everything the page shows."""
    waiting = (
        (
            await db.execute(
                select(ConversationCard)
                .where(ConversationCard.status == "operator_review")
                .order_by(ConversationCard.created_at)
            )
        )
        .scalars()
        .all()
    )
    items = []
    for card in waiting:
        items.append(
            {
                "card": card,
                "turns": await write.load_turns(list(card.source_turn_ids or []), db),
                "parent_title": await _parent_title(card.user_id, db),
            }
        )
    held = (
        (
            await db.execute(
                select(FamilyQuestion)
                .where(FamilyQuestion.status == "held")
                .order_by(FamilyQuestion.created_at)
            )
        )
        .scalars()
        .all()
    )
    recent = (
        (
            await db.execute(
                select(ConversationCard)
                .where(ConversationCard.status != "operator_review")
                .order_by(ConversationCard.updated_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    return {"waiting": items, "held_questions": list(held), "recent": list(recent)}
