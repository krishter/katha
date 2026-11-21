"""
A turn she asks to keep private, after a Story was made from it
(docs/specs/STORIES.md §2.3, CONVERSATION_CARDS.md §5.2).

Consolidation never reads a private turn, but a turn can become private
after the Story it fed was composed: "don't tell my son about the debts"
said a session later covers every turn on the topic. Any Story that cites
such a turn becomes kept_private, whatever its stage, so it is never read
back or shown to the family. An approved Story is hidden the same way.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.story import Story
from stories import correction

logger = logging.getLogger(__name__)


async def keep_stories_private(
    user_id: str, turn_ids: set[uuid.UUID], db: AsyncSession
) -> int:
    """Mark every Story drawing on any of these turns kept_private. Does
    not commit. Returns how many moved."""
    if not turn_ids:
        return 0
    private = {str(t) for t in turn_ids}
    rows = (
        (
            await db.execute(
                select(Story)
                .where(Story.user_id == user_id)
                .where(Story.status == "story")
                .where(Story.approval_status.is_distinct_from("kept_private"))
            )
        )
        .scalars()
        .all()
    )
    moved = 0
    for story in rows:
        if correction.source_turn_ids(story) & private:
            story.approval_status = "kept_private"
            story.notes = [
                *(story.notes or []),
                "Kept private: a source turn became private.",
            ]
            moved += 1
    if moved:
        logger.info(
            "Kept %d stor(y/ies) private for %s: a source turn became private",
            moved,
            user_id,
        )
    return moved
