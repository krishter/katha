from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core import auth
from models.db import get_db

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
    """Consume the magic link token, issue a session JWT, redirect to the dashboard."""
    email, user_id = await auth.verify_magic_link(token, db)
    jwt_token = auth.create_jwt(email, user_id)

    redirect = RedirectResponse(url=f"{settings.APP_BASE_URL}/family", status_code=302)
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
