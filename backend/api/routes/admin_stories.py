"""
/admin/stories/view and its actions (STORIES.md §2.2). Operator token only,
as /admin/cards/view, and every POST carries the same CSRF token.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from cards.review import csrf_ok, csrf_token
from core.auth import require_admin_token_browser
from models.db import get_db
from stories import review, review_page

router = APIRouter(dependencies=[Depends(require_admin_token_browser)])

_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


def _back(flash: str = "") -> RedirectResponse:
    url = "/admin/stories/view"
    if flash:
        url += f"?flash={quote(flash)}"
    return RedirectResponse(url, status_code=303, headers=_HEADERS)


def _check_csrf(csrf: str) -> None:
    if not csrf_ok(csrf):
        raise HTTPException(status_code=403, detail="Forbidden")


async def _act(action) -> RedirectResponse:
    try:
        await action
    except review.ReviewError as exc:
        return _back(str(exc))
    return _back()


@router.get("/admin/stories/view")
async def view(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    queue = await review.review_queue(db)
    flash = request.query_params.get("flash", "")
    return HTMLResponse(
        review_page.render(queue, csrf_token(), flash), headers=_HEADERS
    )


@router.post("/admin/stories/{story_id}/pass")
async def pass_story(
    story_id: uuid.UUID, csrf: str = Form(""), db: AsyncSession = Depends(get_db)
):
    _check_csrf(csrf)
    return await _act(review.pass_story(story_id, db))


@router.post("/admin/stories/{story_id}/rewrite")
async def rewrite(
    story_id: uuid.UUID, csrf: str = Form(""), db: AsyncSession = Depends(get_db)
):
    _check_csrf(csrf)
    return await _act(review.rewrite_readback(story_id, db))


@router.post("/admin/stories/{story_id}/retire")
async def retire(
    story_id: uuid.UUID,
    csrf: str = Form(""),
    reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _check_csrf(csrf)
    return await _act(review.retire(story_id, reason, db))
