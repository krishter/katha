"""
A Story read back to her (docs/specs/STORIES.md §2.3, Stage 2).

A Story waiting for her is one more thing the session's single ask can be
about (cards/asks.py). It shares every cards rule: one ask a session,
never while she is telling something, offered in up to three sessions.
After three offers with no answer it is simply not offered again: it stays
`pending` and never reaches the family.

    story_readback_offer  "I've written down your story about ... shall I
                          read it to you?"
    story_readback        the Story's stored read-back, ending "is this
                          right, and may I share it with Priya?"

The read-back is never written now: it is the text the operator passed,
spoken in her pinned voice. Her answer goes through the cards classifier:

    approve       approved, on the exact text she heard (approve())
    correction    re-composed with her correction as a new version, back
                  to the operator, then read to her once more
                  (stories/correction.py)
    keep_private  kept_private; never shown to the family
    not_now /     stays pending; the offer already counted
    unclear
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import stories
from core import parent_answers, sharing
from models.conversation_card import PARENT_APPROVE
from models.story import Story
from stories import store

logger = logging.getLogger(__name__)

MAX_READBACK_OFFERS = 3  # the cards rule (cards/asks.py, D9)


class NotApproved(RuntimeError):
    """The Story would be approved on words other than those she heard."""


def offer_question(child: str) -> str:
    return (
        "I've written down your story as you told it to me, to share with "
        f"{child}. Shall I read it to you?"
    )


def readback_question(child: str) -> str:
    return f"Is this right, and may I share it with {child}?"


def readable(story: Optional[Story]) -> bool:
    """Passed by the operator, its read-back checked, and its words the ones
    fingerprinted. Only such a Story is ever read to her."""
    return bool(
        story is not None
        and story.status == "story"
        and not story.needs_retelling
        and story.review_status == "passed"
        and story.approval_status == "pending"
        and (story.readback_check or {}).get("result") == "passed"
        and (story.readback_text or "").strip()
        and story.readback_hash
        and story.readback_hash == store.fingerprint(story)
    )


def offerable(story: Story) -> bool:
    """Readable, and not yet offered three times."""
    return readable(story) and (story.readback_offers or 0) < MAX_READBACK_OFFERS


def waiting_since(story: Story) -> Optional[datetime]:
    return story.operator_reviewed_at or story.promoted_at or story.created_at


async def waiting_story(profile, db: AsyncSession) -> Optional[Story]:
    """The Story that has waited longest for her, if any may be offered.
    None when Stories are off for her family, or she said never share
    (§2.3): her threads are still kept."""
    if not stories.enabled_for(profile):
        return None
    if profile.share_preference == sharing.NEVER:
        return None
    rows = (
        (
            await db.execute(
                select(Story)
                .where(Story.user_id == profile.user_id)
                .where(Story.status == "story")
                .where(Story.review_status == "passed")
                .where(Story.approval_status == "pending")
                .where(Story.readback_offers < MAX_READBACK_OFFERS)
            )
        )
        .scalars()
        .all()
    )
    ready = [s for s in rows if offerable(s)]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    ready.sort(key=lambda s: waiting_since(s) or epoch)
    return ready[0] if ready else None


async def get(story_id: Optional[uuid.UUID], db: AsyncSession) -> Optional[Story]:
    if story_id is None:
        return None
    return await db.get(Story, story_id)


def approve(story: Story, *, heard_hash: Optional[str]) -> None:
    """
    Her yes to the read-back. It counts only for the words she heard: the
    fingerprint taken when the read-back was spoken must still be the
    Story's fingerprint now. The CHECK constraint ck_stories_parent_approved
    holds the same rule in the database.
    """
    current = store.fingerprint(story)
    if not heard_hash or heard_hash != story.readback_hash or heard_hash != current:
        raise NotApproved(
            f"story {story.id}: the approval was for different words than the "
            "story now holds"
        )
    if story.review_status != "passed":
        raise NotApproved(f"story {story.id}: not passed by the operator")
    story.approved_hash = heard_hash
    story.approval_status = "approved"
    story.approved_version = story.version
    story.approved_at = datetime.now(timezone.utc)


def record_answer(story: Story, label: str) -> None:
    """Record her classified answer to the read-back and move the Story.
    The turn that carried it is set once the turn is saved."""
    story.parent_answer = label
    story.parent_answered_at = datetime.now(timezone.utc)
    if label == PARENT_APPROVE:
        approve(story, heard_hash=story.readback_hash)
    elif label == "correction":
        story.correction_round = (story.correction_round or 0) + 1
    elif label == "keep_private":
        story.approval_status = "kept_private"
    # not_now / unclear: no move; the offer already counted.


# ── when Katha could not write a correction (§6c) ────────────────────────────


def retell_question() -> str:
    return (
        "I couldn't write one part of your story the way you told it. Would you "
        "like to tell me that part again, or shall we set this story aside?"
    )


def waiting_since_correction(story: Story) -> Optional[datetime]:
    return story.parent_answered_at or story.updated_at or story.created_at


async def waiting_retelling(profile, db: AsyncSession) -> Optional[Story]:
    """The Story waiting longest for her to tell a part again or set it
    aside. She has heard it before, so this is asked even if Stories have
    since been switched off for her family, but never if she said never
    share: then it is simply never shared."""
    if profile.share_preference == sharing.NEVER:
        return None
    rows = (
        (
            await db.execute(
                select(Story)
                .where(Story.user_id == profile.user_id)
                .where(Story.status == "story")
                .where(Story.approval_status == "pending")
                .where(Story.needs_retelling.is_(True))
            )
        )
        .scalars()
        .all()
    )
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return min(rows, key=lambda s: waiting_since_correction(s) or epoch, default=None)


def what_was_unclear(reasons: list[str]) -> str:
    """Why the check cut what it cut, for Katha to name the part as a
    question. Never the cut sentence itself: that may hold a detail she
    did not say."""
    return "; ".join(r for r in reasons if r)[:400]


async def set_aside(story: Story, db: AsyncSession) -> None:
    """She chose to set it aside: retired, never offered or shared again,
    and its pieces go back to the card pool. Does not commit."""
    from sqlalchemy import update

    from models.story_atom import StoryAtom

    story.status = "retired"
    story.retired_reason = "set_aside"
    story.needs_retelling = False
    story.notes = [*(story.notes or []), "She chose to set it aside."]
    await db.execute(
        update(StoryAtom).where(StoryAtom.story_id == story.id).values(story_id=None)
    )


async def classify_retell(transcript: str) -> str:
    result = await parent_answers.classify(
        parent_answers.RETELL, retell_question(), transcript
    )
    return result.label


async def classify_offer(transcript: str, child: str) -> str:
    result = await parent_answers.classify(
        parent_answers.OFFER, offer_question(child), transcript
    )
    return result.label


async def classify_readback(transcript: str, child: str) -> str:
    result = await parent_answers.classify(
        parent_answers.READBACK, readback_question(child), transcript
    )
    return result.label
