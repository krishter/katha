"""
/admin/cards/view and its actions (CONVERSATION_CARDS.md §7.6). Operator
token only, as /admin/metrics/view; every POST also carries a CSRF token
(cards/review.csrf_token), because the browser re-sends Basic credentials
on a form any other site submits.
"""

from __future__ import annotations

import logging
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from cards import review, review_page
from core.auth import require_admin_token_browser
from media import storage
from models.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_token_browser)])

_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


def _back(flash: str = "") -> RedirectResponse:
    url = "/admin/cards/view"
    if flash:
        url += f"?flash={quote(flash)}"
    return RedirectResponse(url, status_code=303, headers=_HEADERS)


def _check_csrf(csrf: str) -> None:
    if not review.csrf_ok(csrf):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/admin/cards/view")
async def view(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    queue = await review.review_queue(db)
    urls = {}
    for item in queue["waiting"]:
        card = item["card"]
        if card.image_s3_key:
            try:
                urls[card.id] = await storage.generate_presigned_url(card.image_s3_key)
            except Exception:
                logger.exception("Could not presign card image %s", card.id)
    flash = request.query_params.get("flash", "")
    return HTMLResponse(
        review_page.render(queue, urls, review.csrf_token(), flash), headers=_HEADERS
    )


async def _act(action) -> RedirectResponse:
    try:
        await action
    except review.ReviewError as exc:
        return _back(str(exc))
    return _back()


@router.post("/admin/cards/{card_id}/approve")
async def approve(
    card_id: uuid.UUID, csrf: str = Form(""), db: AsyncSession = Depends(get_db)
):
    _check_csrf(csrf)
    return await _act(review.approve(card_id, db))


@router.post("/admin/cards/{card_id}/reject")
async def reject(
    card_id: uuid.UUID,
    csrf: str = Form(""),
    reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _check_csrf(csrf)
    return await _act(review.reject(card_id, reason, db))


@router.post("/admin/cards/{card_id}/edit")
async def edit(
    card_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    form = await request.form()
    _check_csrf(str(form.get("csrf", "")))
    fields = {name: str(form[name]) for name in review.EDITABLE_FIELDS if name in form}
    try:
        card = await review.edit(card_id, fields, db)
    except review.ReviewError as exc:
        return _back(str(exc))
    if (card.fidelity_json or {}).get("result") != "passed":
        return _back("Saved, but the edited card did not pass its check; see below")
    return _back()


@router.post("/admin/cards/{card_id}/tag")
async def tag(
    card_id: uuid.UUID,
    csrf: str = Form(""),
    tag: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _check_csrf(csrf)
    return await _act(review.tag_call(card_id, tag, db))


@router.post("/admin/questions/{question_id}/{decision}")
async def resolve_question(
    question_id: uuid.UUID,
    decision: str,
    csrf: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _check_csrf(csrf)
    if decision not in ("release", "drop"):
        raise HTTPException(status_code=404, detail="Not Found")
    return await _act(review.resolve_question(question_id, decision == "release", db))
