from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core import auth
from models.db import get_db
from models.family_account import FamilyAccount

logger = logging.getLogger(__name__)

router = APIRouter()

_COOKIE_NAME = "katha_token"
_GENERIC_MESSAGE = "If that email is registered, a login link is on its way."


@router.post("/auth/magic-link")
async def request_magic_link(
    email: str = Form(...), db: AsyncSession = Depends(get_db)
) -> dict:
    """Always returns 200 — never reveals whether the email is registered."""
    try:
        await auth.send_magic_link(email, db)
    except Exception:
        logger.exception("Error sending magic link")
    return {"message": _GENERIC_MESSAGE}


@router.get("/auth/verify")
async def verify(token: str, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    """
    Consume the magic link token, issue a session JWT, redirect to the dashboard.

    The email link points here directly (this is a full browser navigation,
    not a fetch from the frontend) so the Set-Cookie below lands on this
    backend's own origin without any cross-origin cookie complications. On
    failure we redirect back to the frontend's verify page with an error
    flag rather than raising — a raw JSON 400 here would render as-is in
    the browser instead of the frontend's "link expired" message.
    """
    try:
        email, user_id = await auth.verify_magic_link(token, db)
    except HTTPException:
        return RedirectResponse(
            url=f"{settings.APP_BASE_URL}/family/auth/verify?error=expired",
            status_code=302,
        )
    jwt_token = auth.create_jwt(email, user_id)

    account_result = await db.execute(
        select(FamilyAccount.onboarding_complete).where(
            FamilyAccount.user_id == user_id
        )
    )
    onboarding_complete = account_result.scalar_one_or_none()
    destination = "/family" if onboarding_complete else "/family/onboarding"

    redirect = RedirectResponse(
        url=f"{settings.APP_BASE_URL}{destination}", status_code=302
    )
    redirect.set_cookie(
        key=_COOKIE_NAME,
        value=jwt_token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=60 * 60 * 24 * settings.JWT_EXPIRE_DAYS,
    )
    return redirect


@router.post("/auth/logout")
async def logout() -> RedirectResponse:
    redirect = RedirectResponse(
        url=f"{settings.APP_BASE_URL}/family/login", status_code=302
    )
    redirect.delete_cookie(_COOKIE_NAME)
    return redirect
