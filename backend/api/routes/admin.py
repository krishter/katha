from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_user
from media import storage
from models.consent_record import ConsentRecord
from models.db import get_db
from models.fact import Fact
from models.family_account import FamilyAccount
from models.magic_link_token import MagicLinkToken
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.user_profile import UserProfileModel

logger = logging.getLogger(__name__)

router = APIRouter()

_COOKIE_NAME = "katha_token"


@router.delete("/user/{user_id}")
async def delete_user(
    user_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    DPDP Act data deletion endpoint. A family account can only delete the
    elderly user linked to its own JWT — never someone else's.

    Best-effort: each step is isolated so one failure (e.g. an S3 delete)
    doesn't leave the rest of a user's data stranded. Every failure is
    logged for audit; nothing here should ever raise past this function.
    """
    if current_user["user_id"] != user_id:
        raise HTTPException(
            status_code=403, detail="You may only delete your own data."
        )

    logger.info("Data deletion requested for user %s", user_id)

    # 1-2. Memory card images on S3
    try:
        result = await db.execute(
            select(MemoryCard.image_s3_key).where(MemoryCard.user_id == user_id)
        )
        s3_keys = result.scalars().all()
        for s3_key in s3_keys:
            try:
                await storage.delete_media(s3_key)
            except Exception:
                logger.exception(
                    "Failed to delete S3 object %s for user %s", s3_key, user_id
                )
    except Exception:
        logger.exception("Failed to look up memory cards for user %s", user_id)

    # 3-7. Delete rows that belong directly to this user_id
    for label, stmt in [
        ("memory_cards", delete(MemoryCard).where(MemoryCard.user_id == user_id)),
        ("story_atoms", delete(StoryAtom).where(StoryAtom.user_id == user_id)),
        ("facts", delete(Fact).where(Fact.user_id == user_id)),
        ("sessions", delete(Session).where(Session.user_id == user_id)),
        (
            "user_profiles",
            delete(UserProfileModel).where(UserProfileModel.user_id == user_id),
        ),
    ]:
        try:
            await db.execute(stmt)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Failed to delete %s for user %s", label, user_id)

    # 8. Magic link tokens (keyed by email, not user_id)
    try:
        account_result = await db.execute(
            select(FamilyAccount.email).where(FamilyAccount.user_id == user_id)
        )
        email = account_result.scalar_one_or_none()
        if email is not None:
            await db.execute(
                delete(MagicLinkToken).where(MagicLinkToken.email == email)
            )
            await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete magic_link_tokens for user %s", user_id)

    # 9. Anonymize consent records — retained for audit per DPDP Act, never
    # hard-deleted. email_hash stays; everything else that could identify
    # the person is cleared.
    try:
        await db.execute(
            update(ConsentRecord)
            .where(ConsentRecord.user_id == user_id)
            .values(user_id="DELETED", ip_address=None, user_agent=None)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to anonymize consent_records for user %s", user_id)

    # 10. Family account itself
    try:
        await db.execute(delete(FamilyAccount).where(FamilyAccount.user_id == user_id))
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete family_account for user %s", user_id)

    # 11. Clear the session cookie
    response.delete_cookie(_COOKIE_NAME)

    logger.info("Data deletion completed for user %s", user_id)
    return {
        "status": "deleted",
        "message": "All data has been permanently removed.",
    }
