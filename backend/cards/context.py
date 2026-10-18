"""Building the prompts' CardsContext for a parent (CONVERSATION_CARDS.md §9)."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import cards
from models.family_account import FamilyAccount
from models.user_profile import UserProfileModel
from prompts.system_prompt import CardsContext


async def family_of(user_id: str, db: AsyncSession) -> Optional[FamilyAccount]:
    """The family account linked to her. One per parent for the MVP."""
    return (
        (
            await db.execute(
                select(FamilyAccount)
                .where(FamilyAccount.user_id == user_id)
                .order_by(FamilyAccount.created_at)
            )
        )
        .scalars()
        .first()
    )


async def context_for(
    profile: UserProfileModel, db: AsyncSession
) -> Optional[CardsContext]:
    """None unless cards are on for her: None keeps every prompt as it was."""
    if not cards.enabled_for(profile):
        return None
    family = await family_of(profile.user_id, db)
    return CardsContext(
        child_name=(family.child_name or "") if family else "",
        parent_title=(family.parent_title or "") if family else "",
    )
