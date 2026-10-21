from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from core import auth, erasure
from core.auth import get_current_user
from models.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


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
    """
    if current_user["user_id"] != user_id:
        raise HTTPException(
            status_code=403, detail="You may only delete your own data."
        )

    # The same sweep an operator or the withdrawal hold runs
    # (core/erasure.py), so the three can never drift apart.
    await erasure.erase_user(user_id, db, actor=erasure.ACTOR_FAMILY)

    # Clear the session cookie. Via the shared helper so the domain
    # matches the one it was set with — a mismatch leaves the family logged
    # in with a live cookie immediately after they asked to be erased.
    auth.clear_session_cookie(response)

    return {
        "status": "deleted",
        "message": "All data has been permanently removed.",
    }
