from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core import metrics, metrics_page
from core.auth import require_admin_token, require_admin_token_browser
from models.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Neither the numbers nor the page should be kept by a browser, a proxy, or
# a search engine that somehow found the URL.
_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


async def _read_only_metrics(db: AsyncSession) -> dict:
    """
    Compute the metrics inside a read-only transaction.

    The protection comes first: if the database will not take SET
    TRANSACTION READ ONLY, nothing runs unprotected — the request fails
    with 500 instead. The transaction is rolled back afterwards; it never
    had anything to commit.
    """
    try:
        await db.execute(text("SET TRANSACTION READ ONLY"))
    except Exception:
        logger.exception("Could not make the metrics transaction read-only")
        raise HTTPException(status_code=500, detail="Metrics unavailable")
    try:
        return await metrics.compute_metrics(
            db,
            now=datetime.now(timezone.utc),
            excluded_user_ids=metrics.parse_excluded(
                settings.METRICS_EXCLUDED_USER_IDS
            ),
        )
    finally:
        await db.rollback()


@router.get("/admin/metrics", dependencies=[Depends(require_admin_token)])
async def admin_metrics(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Pilot metrics as JSON (docs/specs/ADMIN_METRICS.md). Operator token only."""
    return JSONResponse(await _read_only_metrics(db), headers=_HEADERS)


@router.get("/admin/metrics/view", dependencies=[Depends(require_admin_token_browser)])
async def admin_metrics_view(db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """The same numbers as one plain HTML page, readable on a phone."""
    page = metrics_page.render(await _read_only_metrics(db))
    return HTMLResponse(page, headers=_HEADERS)
