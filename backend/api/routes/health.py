import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from models.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health(response: Response) -> dict:
    """
    Up means able to serve a parent, which needs the database (P9, M-10):
    it used to answer "ok" with Postgres down. An external monitor polls
    this every 5 minutes. No environment in the body: it told any caller
    which deployment they had reached, and nothing needs it.
    """
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check: database unreachable")
        response.status_code = 503
        return {"status": "unavailable", "database": "unreachable"}
    return {"status": "ok", "version": "0.1.0"}
