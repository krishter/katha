"""
Delete her messages from Twilio (PRE_PILOT_FIX_PLAN P6, M-05; decision D8).

Twilio keeps every message on the account, with its media, until told
otherwise: every voice note she ever sent Katha, and every one Katha sent
her, sat in Twilio's logs outside every erasure. Now:

  - every inbound message is queued for deletion when it arrives, and
    deleted as soon as its voice note has been transcribed (the webhook
    does that after the response, so it never costs the turn);
  - every outbound message is queued when sent, and deleted when Twilio
    reports a final status (the status callback);
  - the scheduler deletes whatever is still queued once it is due:
    INBOUND_GRACE after arrival, OUTBOUND_GRACE after sending, and an hour
    after any failed attempt;
  - erasure deletes everything still queued for the user.

A failed delete is logged at WARNING and stays queued; it never costs the
turn or raises into a caller.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from core import usage
from models.twilio_deletion import TwilioDeletion

logger = logging.getLogger(__name__)

INBOUND = "inbound"
OUTBOUND = "outbound"
INBOUND_GRACE = timedelta(minutes=15)
OUTBOUND_GRACE = timedelta(hours=24)
RETRY_AFTER = timedelta(hours=1)
FINAL_STATUSES = frozenset({"delivered", "read", "failed", "undelivered"})
_BATCH = 100


async def queue(
    message_sid: Optional[str],
    *,
    direction: str,
    user_id: Optional[str] = None,
    delay: Optional[timedelta] = None,
) -> None:
    """Queue a message for deletion. Idempotent on the SID; never raises.
    Patched in tests (backend/conftest.py)."""
    if not message_sid:
        return
    if user_id is None:
        user_id = usage.current_scope().get("user_id")
    if delay is None:
        delay = INBOUND_GRACE if direction == INBOUND else OUTBOUND_GRACE
    try:
        from models.db import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await db.execute(
                insert(TwilioDeletion)
                .values(
                    message_sid=message_sid,
                    user_id=user_id,
                    direction=direction,
                    due_at=datetime.now(timezone.utc) + delay,
                )
                .on_conflict_do_nothing(index_elements=["message_sid"])
            )
            await db.commit()
    except Exception:
        logger.warning(
            "Could not queue Twilio deletion of %s", message_sid, exc_info=True
        )


def _gone(exc: Exception) -> bool:
    """Twilio's 404: it is already deleted, which is what we wanted."""
    return getattr(exc, "status", None) == 404


async def delete_now(message_sid: Optional[str], whatsapp=None) -> bool:
    """
    Delete the message and its media from Twilio now. Returns whether it is
    gone. On failure it stays queued and is retried. Never raises.
    """
    if not message_sid:
        return False
    from models.db import AsyncSessionLocal

    if whatsapp is None:
        from adapters.whatsapp_stub import get_whatsapp_adapter

        whatsapp = get_whatsapp_adapter()
    try:
        await whatsapp.delete_message(message_sid)
        gone, error = True, None
    except Exception as exc:  # noqa: BLE001
        gone, error = _gone(exc), f"{type(exc).__name__}: {exc}"[:500]
        if not gone:
            logger.warning("Could not delete %s from Twilio: %s", message_sid, error)

    try:
        async with AsyncSessionLocal() as db:
            if gone:
                await db.execute(
                    delete(TwilioDeletion).where(
                        TwilioDeletion.message_sid == message_sid
                    )
                )
            else:
                await db.execute(
                    update(TwilioDeletion)
                    .where(TwilioDeletion.message_sid == message_sid)
                    .values(
                        attempts=TwilioDeletion.attempts + 1,
                        last_error=error,
                        due_at=datetime.now(timezone.utc) + RETRY_AFTER,
                    )
                )
            await db.commit()
    except Exception:
        logger.warning(
            "Could not update the Twilio queue for %s", message_sid, exc_info=True
        )
    return gone


async def run_due(now: Optional[datetime] = None, whatsapp=None) -> int:
    """Delete everything that is due. Returns how many went. The scheduler
    runs this every few minutes."""
    from models.db import AsyncSessionLocal

    now = now or datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        sids = (
            (
                await db.execute(
                    select(TwilioDeletion.message_sid)
                    .where(TwilioDeletion.due_at <= now)
                    .order_by(TwilioDeletion.due_at)
                    .limit(_BATCH)
                )
            )
            .scalars()
            .all()
        )
    gone = 0
    for sid in sids:
        gone += await delete_now(sid, whatsapp)
    return gone


async def erase_for_user(user_id: str, db, whatsapp=None) -> tuple[int, list[str]]:
    """
    Delete everything still queued for her, now (core/erasure.py). Returns
    (deleted, SIDs that failed). Failures stay queued under "DELETED", so
    the retry goes on without naming her.
    """
    sids = (
        (
            await db.execute(
                select(TwilioDeletion.message_sid).where(
                    TwilioDeletion.user_id == user_id
                )
            )
        )
        .scalars()
        .all()
    )
    failed = [sid for sid in sids if not await delete_now(sid, whatsapp)]
    if failed:
        await db.execute(
            update(TwilioDeletion)
            .where(TwilioDeletion.user_id == user_id)
            .values(user_id="DELETED")
        )
        await db.commit()
    return len(sids) - len(failed), failed
