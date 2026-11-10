"""
The operator's review of a Story before she hears it (docs/specs/STORIES.md
§2.2, D-a).

    pass      operator_review -> passed. Only a Story whose read-back passed
              its check against the body.
    retire    the Story is set aside for good (retired, reason "operator");
              its atoms go back to the card pool.
    rewrite   write and check the read-back again, for one that failed.

None of these can show the family anything: the furthest a Story gets from
here is "passed", waiting for her yes (Stage 2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cards.context import family_of
from models.story import Story
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel
from stories import consolidate


class ReviewError(ValueError):
    """An action the Story's state does not allow; shown to the operator."""


async def _story(story_id: uuid.UUID, db: AsyncSession) -> Story:
    story = await db.get(Story, story_id)
    if story is None:
        raise ReviewError("No such story")
    return story


def _waiting(story: Story) -> None:
    if story.status != "story" or story.review_status != "operator_review":
        raise ReviewError("That story is not waiting for review")


async def pass_story(story_id: uuid.UUID, db: AsyncSession) -> Story:
    story = await _story(story_id, db)
    _waiting(story)
    if (story.readback_check or {}).get("result") != "passed":
        raise ReviewError("Its read-back has not passed its check; rewrite it first")
    story.review_status = "passed"
    story.operator_reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return story


async def retire(story_id: uuid.UUID, reason: str, db: AsyncSession) -> Story:
    story = await _story(story_id, db)
    if story.status == "retired":
        raise ReviewError("Already retired")
    if story.approval_status == "approved":
        raise ReviewError("She has approved it; it cannot be retired here")
    story.status = "retired"
    story.retired_reason = "operator"
    story.operator_reason = reason.strip() or None
    story.operator_reviewed_at = datetime.now(timezone.utc)
    await db.execute(
        update(StoryAtom).where(StoryAtom.story_id == story.id).values(story_id=None)
    )
    await db.commit()
    return story


async def rewrite_readback(story_id: uuid.UUID, db: AsyncSession) -> Story:
    story = await _story(story_id, db)
    _waiting(story)
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == story.user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise ReviewError("Her profile is gone")
    await consolidate.write_readback_for(story, story.user_id, profile, db)
    return story


async def source_turns(story: Story, db: AsyncSession) -> list[Turn]:
    """Every turn the arc cites, for the page: the operator reads the body
    against what she said."""
    ids = set()
    for element in (story.arc or {}).values():
        if isinstance(element, dict):
            ids |= set(element.get("turn_ids") or [])
    wanted = []
    for value in ids:
        try:
            wanted.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not wanted:
        return []
    rows = await db.execute(
        select(Turn).where(Turn.id.in_(wanted)).order_by(Turn.created_at)
    )
    return list(rows.scalars().all())


async def review_queue(db: AsyncSession) -> dict:
    waiting = (
        (
            await db.execute(
                select(Story)
                .where(Story.status == "story")
                .where(Story.review_status == "operator_review")
                .order_by(Story.promoted_at)
            )
        )
        .scalars()
        .all()
    )
    items = []
    for story in waiting:
        family = await family_of(story.user_id, db)
        items.append(
            {
                "story": story,
                "turns": await source_turns(story, db),
                "parent_title": family.parent_title if family else None,
            }
        )
    recent = (
        (
            await db.execute(
                select(Story)
                .where(Story.status != "thread")
                .where(Story.review_status.is_distinct_from("operator_review"))
                .order_by(Story.updated_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    threads = (
        await db.execute(select(func.count(Story.id)).where(Story.status == "thread"))
    ).scalar_one()
    return {"waiting": items, "recent": list(recent), "threads": threads}
