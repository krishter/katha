from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import session_manager
from core.auth import send_email_ses
from models.family_account import FamilyAccount
from models.session import Session
from models.user_profile import UserProfileModel

logger = logging.getLogger(__name__)

FREE_SESSION_LIMIT = 10
_UPGRADE_PROMPT_COOLDOWN_DAYS = 7

# Tracks the last upgrade-prompt send per user, purely to avoid spamming on
# every subsequent blocked attempt within the cooldown window. In-memory is
# adequate for the pilot's scale (a single-process deployment) — it just
# means the cooldown resets on restart and isn't shared across workers.
# Move this to a DB column if the pilot outgrows a single process.
_last_prompt_sent: dict[str, datetime] = {}


async def get_session_count(user_id: str, db: AsyncSession) -> int:
    """
    How many of the free conversations this user has actually used.

    Counts sessions in which the parent was heard and answered at least
    once — not session rows. The two came apart badly in production: a
    session row is created the moment she sends anything, including when
    every message in it then failed to process, and an inbound message
    arriving after the four-hour stale window opens yet another one. A
    family reached "10 conversations completed" on two real exchanges and
    eight sessions that were nothing but our own errors, and was asked to
    pay.

    exchange_count is the right gate because of where it is incremented:
    session_manager.record_turn runs only after STT, the dialogue call and
    the post-turn policy have all succeeded. Every failure path in
    process_voice_turn returns before it. So exchange_count > 0 means a
    turn genuinely happened, and a session full of failures counts for
    nothing — which is correct, because the product delivered nothing.

    Deliberately not keyed on status == 'completed': a session she talked
    through for six exchanges but never formally closed is a conversation
    she had, and the stale sweep marking it 'abandoned' four hours later
    does not undo that.
    """
    result = await db.execute(
        select(func.count(Session.id))
        .where(Session.user_id == user_id)
        # Shared with the admin metrics page, so the two never disagree.
        # The consent handshake is not one of the free conversations.
        .where(session_manager.is_conversation())
    )
    return result.scalar_one()


async def is_session_allowed(user_id: str, db: AsyncSession) -> bool:
    """
    True if this user can start another session: either their family
    account is on a paid plan, or they haven't hit the free session limit.
    """
    result = await db.execute(
        select(FamilyAccount.plan).where(FamilyAccount.user_id == user_id)
    )
    plan = result.scalar_one_or_none()
    if plan is not None and plan != "free":
        return True

    count = await get_session_count(user_id, db)
    return count < FREE_SESSION_LIMIT


async def send_upgrade_prompt(user_id: str, db: AsyncSession) -> None:
    """
    Email the family account that their free sessions are used up.
    No-ops if a prompt was already sent to this user within the cooldown
    window, or if there's no family account / profile to address it to.
    """
    last_sent = _last_prompt_sent.get(user_id)
    if last_sent is not None and datetime.now(timezone.utc) - last_sent < timedelta(
        days=_UPGRADE_PROMPT_COOLDOWN_DAYS
    ):
        logger.info("Upgrade prompt for %s suppressed (sent recently)", user_id)
        return

    account_result = await db.execute(
        select(FamilyAccount).where(FamilyAccount.user_id == user_id)
    )
    account = account_result.scalar_one_or_none()
    if account is None:
        logger.warning("No family_account for %s — cannot send upgrade prompt", user_id)
        return

    profile_result = await db.execute(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    parent_name = profile.name if profile else "Your parent"

    subject = f"{parent_name} has completed 10 conversations with Katha"
    body_text = (
        f"{parent_name} has shared 10 wonderful sessions with Katha.\n\n"
        "To continue preserving their stories, upgrade to Katha Premium.\n"
        "Reply to this email or visit katha.life/upgrade to continue."
    )
    body_html = (
        f"<p>{parent_name} has shared 10 wonderful sessions with Katha.</p>"
        "<p>To continue preserving their stories, upgrade to Katha Premium. "
        'Reply to this email or visit <a href="https://katha.life/upgrade">'
        "katha.life/upgrade</a> to continue.</p>"
    )
    await send_email_ses(account.email, subject, body_text, body_html)
    _last_prompt_sent[user_id] = datetime.now(timezone.utc)
    logger.info("Sent upgrade prompt to %s for user %s", account.email, user_id)
