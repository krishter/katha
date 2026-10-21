"""
Telling the operator, in time (PRE_PILOT_FIX_PLAN P9, M-10).

A crisis signal was written to a table nobody watched; a vendor outage
degraded every family's conversation unseen; a failed backup only logged.
For an isolated elderly person, a crisis read "tomorrow" is a welfare
failure. So:

  - every crisis signal is emailed at once, with what she said, because
    the operator has to judge it;
  - fallback replies are counted, and the operator is told when more than
    FAILURES_ACROSS_FAMILIES happen in FAILURE_WINDOW, or one family gets
    two in a row: once an hour at most, however many happen;
  - a backup more than BACKUP_STALE_AFTER old is reported once a day;
  - a failed extraction, and a withdrawal (P4, P5), go through here too.

Alerts go only to OPERATOR_EMAIL (required in production). Nothing here
raises: an alert that cannot be sent is logged, and the thing it is about
is already recorded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select

from core.email import send_operator_alert
from models.operator_alert import AlertSent, TurnFailure

logger = logging.getLogger(__name__)

FAILURE_WINDOW = timedelta(hours=1)
FAILURES_ACROSS_FAMILIES = 3  # more than this in the window
FAILURE_ALERT_EVERY = timedelta(hours=1)
BACKUP_STALE_AFTER = timedelta(hours=26)
BACKUP_ALERT_EVERY = timedelta(hours=24)
EXTRACTION_ALERT_EVERY = timedelta(hours=1)
BACKUP_PREFIX = "backups/postgres/"


async def send(
    subject: str,
    body: str,
    *,
    key: Optional[str] = None,
    every: Optional[timedelta] = None,
) -> bool:
    """Email the operator. With `key` and `every`, at most once per `every`
    for that key. Returns whether it was sent. Never raises."""
    from models.db import AsyncSessionLocal

    try:
        if key and every:
            async with AsyncSessionLocal() as db:
                last = (
                    await db.execute(
                        select(func.max(AlertSent.sent_at)).where(AlertSent.key == key)
                    )
                ).scalar_one()
            if last is not None and datetime.now(timezone.utc) - last < every:
                logger.info("Alert %r suppressed: sent within %s", key, every)
                return False
        await send_operator_alert(subject, body)
        if key:
            async with AsyncSessionLocal() as db:
                db.add(AlertSent(key=key))
                await db.commit()
        return True
    except Exception:
        logger.exception("Could not send the operator alert %r", key or subject)
        return False


async def crisis(
    *,
    user_id: str,
    session_id: str,
    turn_id,
    language: str,
    matched_pattern: str,
    transcript: str,
    source: str,
) -> None:
    """Every crisis signal, at once. Her words are in the email because the
    operator has to judge them; they go nowhere else (P7)."""
    await send(
        "Crisis signal: read this today",
        f"A crisis pattern matched in a conversation. Read it, and call the "
        f"family if it is real.\n\n"
        f"Family (user_id): {user_id}\n"
        f"Session: {session_id}\n"
        f"Turn: {turn_id}\n"
        f"Language: {language}\n"
        f"Matched: {matched_pattern!r} (in {source})\n\n"
        f"What was said:\n{transcript}\n\n"
        f"Katha gave the crisis response with the iCall number (9152987821).",
    )


async def turn_failed(user_id: Optional[str], stage: str) -> None:
    """Count a fallback reply, and tell the operator if failures are piling
    up. Never raises. Patched in tests (backend/conftest.py)."""
    from models.db import AsyncSessionLocal
    from models.turn import Turn

    try:
        now = datetime.now(timezone.utc)
        since = now - FAILURE_WINDOW
        async with AsyncSessionLocal() as db:
            previous = None
            if user_id:
                previous = (
                    await db.execute(
                        select(func.max(TurnFailure.created_at))
                        .where(TurnFailure.user_id == user_id)
                        .where(TurnFailure.created_at >= since)
                    )
                ).scalar_one()
            db.add(TurnFailure(user_id=user_id, stage=stage))
            await db.commit()
            across = (
                await db.execute(
                    select(func.count(TurnFailure.id)).where(
                        TurnFailure.created_at >= since
                    )
                )
            ).scalar_one()
            in_a_row = False
            if previous is not None:
                # Two in a row: nothing went right for her in between.
                answered_since = (
                    await db.execute(
                        select(func.count(Turn.id))
                        .where(Turn.user_id == user_id)
                        .where(Turn.created_at > previous)
                    )
                ).scalar_one()
                in_a_row = answered_since == 0
    except Exception:
        logger.exception("Could not count a turn failure (%s)", stage)
        return

    if across > FAILURES_ACROSS_FAMILIES or in_a_row:
        reason = (
            f"{across} fallback replies in the last hour across all families"
            if across > FAILURES_ACROSS_FAMILIES
            else f"two fallback replies in a row for {user_id}"
        )
        await send(
            "Turns are failing",
            f"{reason}. The latest was at the {stage} stage. Parents are "
            "hearing an apology instead of a conversation: check the vendors "
            "(Sarvam, Anthropic, Gemini) and the backend log.",
            key="turn-failures",
            every=FAILURE_ALERT_EVERY,
        )


async def extraction_failed(turn_id, user_id: str) -> None:
    """P5: a turn whose extraction, or privacy check, did not complete."""
    await send(
        "A turn's extraction failed",
        f"Turn {turn_id} for {user_id}: its extraction, or the check for a "
        "privacy request in it, did not complete. Its stories stay hidden "
        "from the family until it is re-run.",
        key="extraction-failed",
        every=EXTRACTION_ALERT_EVERY,
    )


async def check_backup(now: Optional[datetime] = None, newest=None) -> bool:
    """
    Report a backup that is missing or more than BACKUP_STALE_AFTER old.
    scripts/backup.sh writes one under backups/postgres/ every night; this
    reads the bucket rather than trusting the script to report its own
    failure. Returns whether the backup is fresh.
    """
    from media import storage

    now = now or datetime.now(timezone.utc)
    try:
        if newest is None:
            newest = await storage.newest_modified(BACKUP_PREFIX)
    except Exception:
        logger.exception("Could not list backups")
        newest = None
    if newest is not None and now - newest <= BACKUP_STALE_AFTER:
        return True
    when = newest.isoformat() if newest else "never (no backup found)"
    await send(
        "Backups have stopped",
        f"The newest database backup under s3://…/{BACKUP_PREFIX} is from "
        f"{when}. Check the backup container: "
        "docker compose -f docker-compose.prod.yml logs backup",
        key="backup-stale",
        every=BACKUP_ALERT_EVERY,
    )
    return False
